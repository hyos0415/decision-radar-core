"""중요도 결정표 (importance-policy §4) — 코드. 모델 없이 결정론적으로 돈다 (CLAUDE.md 규율 1).

등급(L0~L8) × 변화 종류 → 남김(✅) / 버림(❌) / 해당 없음(—). 「—」는 그 조합이 정책에 없다는 뜻이고 결과는 버림이다 (소음 우선 — §6).
표는 문서의 §4 표와 같아야 한다 — test_importance.py가 문서 표를 읽어 코드와 대조한다. 바꾸지 않는다 (규율 7).

적용 순서 (§4 「보정 축 적용 순서」 + §8 판정 세부): 등급으로 행을 고른다 → 경로가 개발이면 security만 남기고 거기서 끝(개발 경로가 최우선 — 아래 보정이 되살리지 않는다)
→ 아니면 떠 있는 태그면 minor·patch도 ✅ → 민감 경로면 security·deprecation을 ✅로.
두 등급이면 두 행을 모두 적용하고 하나라도 ✅면 남긴다 — 어느 행(등급 × 변화) 때문에 남았는지 `kept_by`에 기록한다.
주당 상한: 등급 높은 순(L0→L8) → 변화 심각도 순. 잘린 것은 `truncated`로 돌려줘 로그에 남기게 한다.
"""

import re

TIERS = [f"L{i}" for i in range(9)]
CHANGES = ["breaking", "deprecation", "eol", "security", "yanked", "major", "minor_patch", "model_retirement", "pricing", "successor_model"]
# 모델이 아니라 스크립트가 붙이는 변화 (§3 변경 2026-10-16, HYO-129) — 후속 모델은 successor.py가 제목으로 판정한다.
# 모델에게 묻는 목록(MODEL_CHANGES)은 이전과 같다: 프롬프트·스키마·Jev 질문이 바뀌지 않는다
SCRIPT_CHANGES = frozenset({"successor_model"})
MODEL_CHANGES = [c for c in CHANGES if c not in SCRIPT_CHANGES]
CHANGE_LABEL = {"breaking": "breaking", "deprecation": "deprecation", "eol": "우리 버전 EOL", "security": "security", "yanked": "yanked",
                "major": "major", "minor_patch": "minor·patch", "model_retirement": "모델 은퇴", "pricing": "가격·한도 변경",
                "successor_model": "후속 모델"}
TIER_LABEL = {"L0": "언어 런타임", "L1": "실행 기반", "L2": "호스팅 모델", "L3": "호스팅 서비스", "L4": "앱 프레임워크",
              "L5": "공급자 SDK", "L6": "도메인 라이브러리", "L7": "개발·테스트 도구", "L8": "유틸"}

KEEP, DROP, NA = "✅", "❌", "—"

# 열 순서 = CHANGES
_ROWS = {
    "L0": "✅ ✅ ✅ ✅ — ❌ ❌ — — —",
    "L1": "✅ ✅ ✅ ✅ ✅ ✅ ❌ — — —",
    "L2": "✅ ✅ — — — — ❌ ✅ ✅ ✅",
    "L3": "✅ ✅ — ✅ — — ❌ — ✅ —",
    "L4": "✅ ✅ ✅ ✅ ✅ ✅ ❌ — — —",
    "L5": "✅ ✅ ❌ ✅ ✅ ✅ ❌ — — —",
    "L6": "✅ ✅ ❌ ✅ ✅ ❌ ❌ — — —",
    "L7": "❌ ❌ ❌ ✅ ❌ ❌ ❌ — — —",
    "L8": "❌ ❌ ❌ ✅ ✅ ❌ ❌ — — —",
}
TABLE = {t: dict(zip(CHANGES, row.split())) for t, row in _ROWS.items()}

# 변화 심각도 (낮을수록 먼저): security > breaking > 우리 버전 EOL·모델 은퇴 > deprecation > 나머지 > 후속 모델 (§4 주당 상한)
# 후속 모델은 「선택 실험」 — 지금 모델은 계속 돈다. 맨 뒤 (§4 변경 2026-10-16, HYO-129)
SEVERITY = {"security": 0, "breaking": 1, "eol": 2, "model_retirement": 2, "deprecation": 3, "successor_model": 5}
SEVERITY_REST = 4
WEEKLY_LIMIT = 5

# 관문이 판정 못 한 비율 경고 (HYO-122 ⑤ — 592건이 요약에 안 보이고 조용히 빠졌다). (모르는 기술 + 분류 불가) / (레인 A − 팀원 스택)이 넘으면 경고.
# 잠정 (2026-10-15, 사용자 — 아직 재 보지 않은 첫 제안. 고치기 전 W42 ≈ 81%). HYO-122 PR 4 뒤 W42(+W41)를 다시 돌려 정상 비율을 보고
# 그보다 조금 높게 다시 정한다. 바꿀 때는 지우지 않고 `변경 (날짜, 이유)`로 남긴다.
# UNKNOWN_WARN_RATIO = 0.20
# 변경 (2026-10-15, 사용자 — HYO-122 PR 4 뒤 다시 잰 정상 비율: W41 0.9% (2/222) · W42 0.8% (2/264)): 20% → 5%. 정상의 약 6배 —
# 새 기술 하나가 통째로 빠지면(llama.cpp는 W42에 128건) 바로 걸리고, 분류 불가 몇 건으로는 안 울린다. 두 주뿐인 수치라 여전히 잠정
UNKNOWN_WARN_RATIO = 0.05


def severity(change):
    return SEVERITY.get(change, SEVERITY_REST)


def tier_rank(tier):
    return TIERS.index(tier)


class Context:
    """보정 축 값. 모두 기본은 보정 없음. path: "prod"|"dev" · pin: None|"floating"|"range"|"pinned" · sensitive · local (로컬 서빙 모델)."""

    def __init__(self, path="prod", pin=None, sensitive=False, local=False):
        self.path, self.pin, self.sensitive, self.local = path, pin, bool(sensitive), bool(local)

    def as_dict(self):
        return {"path": self.path, "pin": self.pin, "sensitive": self.sensitive, "local": self.local}


def cell(tier, change, ctx=None):
    """한 칸 → {"tier","change","base","keep","adjusted_by"}. base는 표의 값, keep은 보정 뒤의 결과."""
    ctx = ctx or Context()
    base = TABLE[tier][change]
    steps = []
    if ctx.local and tier == "L2" and change == "model_retirement":   # v1.1 U2: 모델 은퇴 칸은 호스팅 모델에만
        base = NA
        steps.append("로컬 서빙 모델은 모델 은퇴 칸 해당 없음 (v1.1 U2)")
    keep = base == KEEP
    if ctx.path == "dev":
        # 개발 경로가 최우선 (사용자 2026-10-06, importance-policy §8 판정 세부) — security만 남기고 떠 있는 태그·민감 경로 보정이 되살리지 않는다
        new = change == "security"
        if new != keep:
            steps.append("경로=개발 → security만 남김")
        return {"tier": tier, "change": change, "base": base, "keep": new, "adjusted_by": steps}
    if ctx.pin == "floating" and change == "minor_patch" and not keep:
        keep = True
        steps.append("떠 있는 태그 → minor·patch도 남김")
    if ctx.sensitive and change in ("security", "deprecation") and not keep:
        keep = True
        steps.append("민감 경로 → security·deprecation 남김")
    return {"tier": tier, "change": change, "base": base, "keep": keep, "adjusted_by": steps}


# 프리릴리스 버전 표기 — 제목에서 찾는다. `v45.0.0-beta.2` · `2.14.0b1` · `5.0.0rc1` · `1.79.1.dev2` · `v1.80.12-nightly` · `3.1.0a1`
_PRERELEASE = re.compile(r"\d(?:[-.]?(?:alpha|beta|rc|dev|pre|preview|canary|next|nightly)\.?\d*|(?:a|b)\d+)(?![\w.])|\bnightly\b", re.I)


def is_prerelease(title):
    return bool(_PRERELEASE.search(title or ""))


def normalize_changes(changes, title=""):
    """모델이 짚은 변화 종류 → 정의대로 정리한 목록 (§3 변경 2026-10-21 ③④를 스크립트로 집행 — 규율 1, HYO-131).
    ③ 제목이 프리릴리스(alpha·beta·rc·dev·nightly)면 major를 뺀다 ④ 다른 종류가 있으면 minor_patch를 뺀다. 순서는 그대로.
    둘 다 빼기만 한다 — 모델이 안 짚은 종류를 더하지 않는다. 다 빠져 비면 [] (decide가 minor·patch로 친다)."""
    out = [c for c in changes if not (c == "major" and is_prerelease(title))]
    if any(c != "minor_patch" for c in out):
        out = [c for c in out if c != "minor_patch"]
    return out


def decide(tiers, changes, ctx=None):
    """기술의 등급(들) × 소식의 변화(들) → {"keep", "rows", "kept_by", "defaulted_change"}.

    변화가 비어 있으면(모델이 해당 종류를 못 찾음) minor·patch로 친다 — 표의 「그 외」. 등급이 없으면 판정하지 않는다(호출부가 걸러야 한다).
    """
    if not tiers:
        raise ValueError("등급이 없다 — 분류 불가는 호출부가 후보에서 빼고 기록한다")
    defaulted = not changes
    changes = list(changes) or ["minor_patch"]
    rows = [cell(t, c, ctx) for t in tiers for c in changes]
    kept_by = [r for r in rows if r["keep"]]
    return {"keep": bool(kept_by), "rows": rows, "kept_by": kept_by, "defaulted_change": defaulted}


def rank_key(entry):
    """주당 상한 정렬 키: 남긴 행들 중 가장 높은 등급 → 가장 심각한 변화 → id (결정론)."""
    kb = entry["kept_by"]
    return (min(tier_rank(r["tier"]) for r in kb), min(severity(r["change"]) for r in kb), str(entry.get("id", "")))


def apply_cap(entries, limit=WEEKLY_LIMIT):
    """남긴 후보 → (보이는 것, 잘린 것, "5/N"). entries는 {"id", "kept_by", ...}. 잘린 것은 버리지 않고 호출부가 로그에 남긴다."""
    ordered = sorted(entries, key=rank_key)
    shown, cut = ordered[:limit], ordered[limit:]
    return shown, cut, f"{len(shown)}/{len(ordered)}"


def gate_counts(entries, warn_ratio=None):
    """레인 A 항목들(각각 `gate` 칸) → 요약 건수 + 경고. 모르는 기술 = unclassified인데 걸린 기술이 없음, 분류 불가 = 기술은 있는데 등급이 없음.
    ratio = (모르는 기술 + 분류 불가) / (레인 A − 팀원 스택). 분모가 0이면 None (경고 없음)."""
    warn_ratio = UNKNOWN_WARN_RATIO if warn_ratio is None else warn_ratio
    c = {"lane_a": len(entries), "kept": 0, "unknown": 0, "unresolved": 0, "other_stack": 0, "error": 0}
    for e in entries:
        g = e.get("gate") or {}
        s = g.get("status")
        if g.get("keep"):
            c["kept"] += 1
        if s == "unclassified":
            c["unresolved" if g.get("technologies") else "unknown"] += 1
        elif s == "other-stack":
            c["other_stack"] += 1
        elif s == "error":
            c["error"] += 1
    base = c["lane_a"] - c["other_stack"]
    c["ratio"] = (c["unknown"] + c["unresolved"]) / base if base else None
    c["warn_ratio"] = warn_ratio
    c["warn"] = c["ratio"] is not None and c["ratio"] > warn_ratio
    return c

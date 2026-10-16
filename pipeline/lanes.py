"""두 레인 분리 (doc-pipeline §2-3).

레인 A = 내 스택에 일어난 일 (의존성 릴리스 · 벤더 피드 · 벤더 페이지 변경). 판정은 importance-policy 관문의 몫이다.
레인 B = 새로 볼 만한 것 (새 모델 · repo · 글 · 논문). 프로필 점수로 순위를 낸다.

importance-policy 관문(기술 등급 × 변화 종류)은 아직 코드에 없다 — radar.py에도 collector/에도 없고,
policy는 문서(`importance-policy.md`)와 tier-screen 시험뿐이다. 그래서 여기서는 「스택 기술에 걸린 항목」
분류까지만 하고 `gate`는 비워 둔다 (gate_status = "not-implemented"). 관문이 생기면 GATE 자리에 꽂는다.

분류는 수집기가 붙인 통로(channel)로 한다 — 수집기가 이미 「스택 의존성의 releases.atom」 「규칙이 뽑은 벤더」만 가져오기 때문.
제목에 스택 이름이 나오는 다른 출처의 항목은 레인을 옮기지 않고 `stack_mentions` 표시만 한다 (판단은 사용자 몫).
"""
import re

GATE = None   # importance-policy 관문이 생기면 (item) -> {"keep": bool, "tier": ..., "change": ...}를 여기에


def classify(item):
    """항목 하나 → (레인, 이유 목록). 레인은 "A" · "B" · None(분석 대상 아님: 벤더 페이지 원본)."""
    meta = item.get("meta") or {}
    kind = meta.get("kind")
    channels = item.get("channels") or []
    if kind == "page":
        return None, ["page-snapshot"]            # 페이지 전문은 비교 재료지 소식이 아니다. 바뀐 것은 page-diff 항목으로 온다
    reasons = []
    if kind == "page-diff":
        reasons.append(f"page-diff:{meta.get('vendor', '?')}")
    if "dependency-releases" in channels:
        reasons.append(f"dependency-release:{meta.get('dependency') or meta.get('repo') or '?'}")
    for ch in channels:
        if ch.startswith("vendor:") and kind != "page-diff":
            reasons.append(f"vendor-feed:{ch.split(':', 1)[1]}")
    return ("A", reasons) if reasons else ("B", [])


def split_lanes(items):
    """겹치지 않게 나눈다. 한 항목은 한 레인에만 있다 (A에 걸리면 A)."""
    lane_a, lane_b, skipped = [], [], []
    for it in items:
        lane, reasons = classify(it)
        if lane == "A":
            lane_a.append((it, reasons))
        elif lane == "B":
            lane_b.append(it)
        else:
            skipped.append((it, reasons))
    return lane_a, lane_b, skipped


def stack_mentions(item, terms):
    """제목·URL에 스택 이름이 나오는가 (표시만). 단어 경계, 대소문자 무시."""
    hay = f"{item.get('title', '')} {item.get('url', '')}"
    out = []
    for t in terms:
        if re.search(r"(?<![0-9A-Za-z])" + re.escape(t) + r"(?![0-9A-Za-z])", hay, re.I):
            out.append(t)
    return out


def stack_terms_from_claude_md(text):
    """대상 repo CLAUDE.md 「의존성 → component」 표의 이름들 (npm 행 포함 — 표시용). 이름 규칙은 수집기와 같은 파서 (HYO-122 ⑥)."""
    from collector.sources.github import dependency_rows_from_claude_md
    out = []
    for row in dependency_rows_from_claude_md(text):
        for name in row["names"]:
            if len(name) >= 3 and name not in out:
                out.append(name)
    return out


def clean_term(name, max_len=40):
    """ADR technology 문구에서 표시용 이름만: 괄호 설명을 떼고, 문장 같은 것(길거나 `+`가 든 것)은 버린다."""
    t = re.sub(r"\s*\(.*?\)", "", name).strip()
    return t if 3 <= len(t) <= max_len and "+" not in t else None

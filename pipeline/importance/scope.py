"""우리 것인가 — 특정 버전·특정 모델에 일어나는 변화를 우리 것으로 좁힌다 (importance-policy §3·§4 변경 2026-10-27, HYO-148).

모델은 그대로 「어떤 버전의 지원 종료(eol)」·「어떤 모델의 은퇴(model_retirement)」를 짚는다. 그게 우리 버전·우리 모델인지는 여기서 스크립트가 정한다 (규율 1).
빼기만 하고 더하지 않는다 — policy.normalize_changes와 같은 원칙.

  eol              지원 종료 문장에서 (기술, 버전, 이하인가)를 뽑아 우리 버전(signals.read_repo_versions · 사람 overrides `version`)과 견준다
                   주어 꼴: 「Electron 42.x.y has reached end-of-support」 · 「Python 3.9 is no longer supported」 · 「Vite 7 and below are no longer supported」
                   목적어 꼴: 「drops support for Python 3.9」 · 「Removing Python 3.9 support」 · 「Drop Python 3.9」
                   이름 없는 줄 표기(「42.x.y has reached end-of-support」)는 그 행의 기술로 본다
                   우리 줄 → 유지 · 다른 줄만 → 뺌 · 못 뽑았거나 우리 버전을 모름 → 유지하고 unknown으로 남긴다 (사용자 Q1, 2026-10-27)
  model_retirement L2 기술의 계열 + 같은 버전이 제목·본문에 나와야 유지 (successor.mentions). 이름에 버전이 없는 기술(Amazon Bedrock)은 → 유지 · unknown
그 밖의 종류(security 포함)는 빼지 않는다. 의존성 릴리스가 우리 줄인지(release_line)는 기록만 한다 (사용자 Q2).
비교 깊이: 문장에 적힌 버전의 자릿수만큼 (「42」 → 첫 숫자, 「3.9」 → 둘째 숫자까지). 릴리스 버전에는 semver 줄(첫 숫자, 0.x면 둘째까지) + LINE_DEPTH.
"""
import re

from . import successor
from .signals import norm

SCOPED = ("eol", "model_retirement")
LINE_DEPTH = {"python": 2}      # 줄이 둘째 숫자까지인 기술 (Python 3.12 → 3.13은 다른 줄). 나머지는 semver 줄

_VER = r"v?(\d+(?:\.\d+)*)(?:\.[xX*])*(?:\.y)?(?!\d)"
_LE = r"(?:\s*(?:and|or)\s+(below|older|earlier|lower|prior))?"
_GONE = r"(?:end[- ]of[- ](?:life|support)|\bEOL\w*|no longer supported|unsupported)"
_BE = r"[^.;\n]{0,40}?\b(?:is|are|was|were|has|have)\b[^.;\n]{0,25}?" + _GONE
_DROP = r"\b(?:drop(?:s|ped|ping)?|remov(?:e|es|ed|ing)|end(?:s|ed)?)\s+(?:support\s+for\s+|the\s+)?"
_BARE = re.compile(r"(?<![\w.])" + r"v?(\d+(?:\.\d+)*)(?:\.[xX*])+(?:\.y)?(?!\d)" + _LE + _BE, re.I)


def vtuple(s):
    """'39.2.6' → (39, 2, 6). 숫자가 없으면 None."""
    m = re.search(r"\d+(?:\.\d+)*", str(s or ""))
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def _name_rx(name):
    """기술 이름 → 문장에서 찾을 이름 정규식 | None. 이미지·스코프 이름(`python:3.12-slim` · `@types/node`)은 문장에 그 꼴로 나오지 않아 보지 않는다."""
    if any(c in name for c in ":/@"):
        return None
    toks = [re.escape(t) for t in norm(name).split("-") if t]
    return r"(?<![\w@/.-])" + r"[\s._-]?".join(toks) if toks else None


def eol_targets(text, names, own):
    """글 → [(기술 이름, 버전 튜플, "eq"|"le")]. names = 견줄 기술 이름(버전을 아는 것) · own = 이 행의 기술 (이름 없는 줄 표기용)."""
    out = []
    for n in dict.fromkeys(norm(x) for x in list(names) + ([own] if own else [])):
        rx = _name_rx(n)
        if not rx:
            continue
        nv = rx + r"[\s-]?" + _VER + _LE
        for pat in (nv + _BE, _DROP + nv):
            for m in re.finditer(pat, text or "", re.I):
                t = (n, vtuple(m.group(1)), "le" if m.group(2) else "eq")
                if t not in out:
                    out.append(t)
    if own:
        for m in _BARE.finditer(text or ""):
            t = (norm(own), vtuple(m.group(1)), "le" if m.group(2) else "eq")
            if t not in out:
                out.append(t)
    return out


def line_match(ours, announced, op="eq"):
    """우리 버전(튜플) vs 문장의 버전 → True(우리 줄) | False(다른 줄) | None(우리 버전이 그 자릿수까지 없다 — `>=3`과 「3.9」)."""
    d = len(announced)
    if len(ours) < d:
        return None
    return ours[:d] <= announced if op == "le" else ours[:d] == announced


def line_depth(name, version):
    return LINE_DEPTH.get(norm(name)) or (2 if version and version[0] == 0 else 1)


def release_version(item):
    """의존성 릴리스 제목의 마지막 버전 표기 → 튜플 | None (`electron v42.11.17` → (42, 11, 17) · llama.cpp `b6512`는 없음)."""
    if not (item.get("meta") or {}).get("dependency"):
        return None
    found = re.findall(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)*)", item.get("title") or "")
    return vtuple(found[-1]) if found else None


def _eol(tech, text, ours_of):
    """→ (verdict "ours"|"other"|"unknown", 근거 목록). 근거 = [{technology, announced, op, ours, match}]."""
    known = [n for n in ours_of.names() if ours_of(n)]
    ev, verdicts = [], []
    for n, ann, op in eol_targets(text, known, tech):
        ours = [v for v in (vtuple(x) for x in ours_of(n)) if v]
        ms = [line_match(o, ann, op) for o in ours]
        m = True if True in ms else (False if ms and all(x is False for x in ms) else None)
        ev.append({"technology": n, "announced": ".".join(map(str, ann)), "op": op, "ours": ours_of(n), "match": m})
        verdicts.append(m)
    if True in verdicts:
        return "ours", ev
    if verdicts and all(v is False for v in verdicts):
        return "other", ev
    return "unknown", ev


def _all_older(ours, announced):
    """우리 버전이 전부 문장의 줄보다 옛 줄인가 — 「새 줄이 지원 종료됐는데 우리는 그보다 옛 줄」(우리 줄도 지원 밖일 수 있다 — 판정이 아니라 알림)."""
    vs = [vtuple(x) for x in ours]
    return bool(vs) and all(v and v[:len(announced)] < announced for v in vs)


def narrow(labels, tech, tiers, item, ours_of):
    """한 기술의 변화 라벨 → (정리한 라벨, 기록 목록). 기록 = [{change, verdict, ...}] — 무엇을 왜 뺐나/남겼나.
    ours_of(name) → 우리 버전 문자열 목록 · ours_of.names() → 버전을 아는 기술 이름들."""
    text = (item.get("title") or "") + "\n" + (item.get("text") or "")
    out, notes = [], []
    for c in labels:
        if c == "eol":
            verdict, ev = _eol(tech, text, ours_of)
            note = {"change": c, "verdict": verdict, "evidence": ev}
            newer = [e for e in ev if e["match"] is False and e["op"] == "eq" and _all_older(e["ours"], vtuple(e["announced"]))]
            if verdict == "other" and newer:
                note["newer_line_eol"] = [{"technology": e["technology"], "ours": e["ours"], "announced": e["announced"]} for e in newer]
            notes.append(note)
            if verdict == "other":
                continue
        elif c == "model_retirement" and "L2" in (tiers or []):
            hit = successor.mentions(text, tech)
            verdict = "unknown" if hit is None else ("ours" if hit else "other")
            notes.append({"change": c, "verdict": verdict, "mentioned": hit or None})
            if verdict == "other":
                continue
        out.append(c)
    return out, notes


def release_line(tech, item, ours_of):
    """의존성 릴리스가 우리 줄인가 — 기록만 한다 (판정에 안 쓴다, 사용자 Q2). → {release, ours, line "ours"|"other"} | None."""
    rel = release_version(item)
    ours = [v for v in (vtuple(x) for x in ours_of(tech)) if v]
    if not rel or not ours:
        return None
    d = line_depth(tech, rel)
    same = any(len(o) >= d and o[:d] == rel[:d] for o in ours)
    return {"release": ".".join(map(str, rel)), "ours": ours_of(tech), "line": "ours" if same else "other"}


class Versions:
    """우리 버전 조회. versions = {정규화 이름: [{version, ...}]} (signals.read_repo_versions) · human = {정규화 이름: [버전 문자열]} (overrides — 이긴다)."""

    def __init__(self, versions=None, human=None):
        self.auto = {norm(k): list(dict.fromkeys(r["version"] for r in v if r.get("version"))) for k, v in (versions or {}).items()}
        self.human = {norm(k): list(dict.fromkeys(v)) for k, v in (human or {}).items() if v}

    def __call__(self, name):
        k = norm(name)
        return self.human.get(k) or self.auto.get(k) or []

    def names(self):
        return list(dict.fromkeys(list(self.human) + list(self.auto)))

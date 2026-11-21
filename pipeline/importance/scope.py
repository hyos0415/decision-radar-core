"""우리 것인가 — 특정 버전·특정 모델에 일어나는 변화를 우리 것으로 좁힌다 (importance-policy §3·§4 변경 2026-10-27, HYO-148).

모델은 그대로 「어떤 버전의 지원 종료(eol)」·「어떤 모델의 은퇴(model_retirement)」를 짚는다. 그게 우리 버전·우리 모델인지는 여기서 스크립트가 정한다 (규율 1).
빼기만 하고 더하지 않는다 — policy.normalize_changes와 같은 원칙.

  eol              지원 종료 문장에서 (기술, 버전, 이하인가)를 뽑아 우리 버전(signals.read_repo_versions · 사람 overrides `version`)과 견준다
                   주어 꼴: 「Electron 42.x.y has reached end-of-support」 · 「Python 3.9 is no longer supported」 · 「Vite 7 and below are no longer supported」
                   목적어 꼴: 「drops support for Python 3.9」 · 「Removing Python 3.9 support」 · 「Drop Python 3.9」
                   이름 없는 줄 표기(「42.x.y has reached end-of-support」)는 그 행의 기술로 본다
                   우리 줄 → 유지 · 다른 줄만 → 뺌 · 못 뽑았거나 우리 버전을 모름 → 유지하고 unknown으로 남긴다 (사용자 Q1, 2026-10-27)
  model_retirement L2 기술의 계열 + 같은 버전이 제목·본문에 나와야 유지 (successor.mentions). 이름에 버전이 없는 기술(Amazon Bedrock)은 → 유지 · unknown
  breaking         릴리스 노트의 Breaking 절이 「언어 런타임 버전 지원 중단」(RUNTIMES) 줄뿐이고, 이 기술을 쓰는 대상 repo 전부의 런타임 버전을 알며
                   모두 그 버전보다 새 줄이면 → 뺌 (HYO-202, W47 「Drop support for Python 3.10」 3건). repo 하나라도 런타임 버전을 모르거나,
                   절에 다른 항목이 있거나, 절이 없으면(첫 문장에만 적힌 꼴 — 무엇이 breaking인지 스크립트가 모른다) → 유지.
                   절이 없는 글은 모델이 글에서 그대로 옮긴 breaking 근거 문장(basis — Backend.breaking_basis)을 항목으로 본다 (HYO-208):
                   문장이 전부 글에 그대로 있고 · 전부 런타임 지원 중단 문장이나 그 곁문장(최소 버전 · 끊긴 버전 사용자 안내)이고 · 모델이
                   그 밖의 이유가 더 있다고 하지 않았을 때만 → 뺌. 판정은 여전히 스크립트가 한다
그 밖의 종류(security 포함)는 빼지 않는다. 의존성 릴리스가 우리 줄인지(release_line)는 기록만 한다 (사용자 Q2).
비교 깊이: 문장에 적힌 버전의 자릿수만큼 (「42」 → 첫 숫자, 「3.9」 → 둘째 숫자까지). 릴리스 버전에는 semver 줄(첫 숫자, 0.x면 둘째까지) + LINE_DEPTH.
"""
import re

from . import successor
from .signals import norm

SCOPED = ("eol", "model_retirement", "breaking")
LINE_DEPTH = {"python": 2}      # 줄이 둘째 숫자까지인 기술 (Python 3.12 → 3.13은 다른 줄). 나머지는 semver 줄

_VER = r"v?(\d+(?:\.\d+)*)(?:\.[xX*])*(?:\.y)?(?!\d)"
_LE = r"(?:\s*(?:and|or)\s+(below|older|earlier|lower|prior))?"
_GONE = r"(?:end[- ]of[- ](?:life|support)|\bEOL\w*|no longer supported|unsupported)"
_BE = r"[^.;\n]{0,40}?\b(?:is|are|was|were|has|have)\b[^.;\n]{0,25}?" + _GONE
_DROP = r"\b(?:drop(?:s|ped|ping)?|remov(?:e|es|ed|ing)|end(?:s|ed)?)\s+(?:support\s+for\s+|the\s+)?(?:EOL(?:'?e?d)?\s+)?"   # 「Remove EOL'd Python 3.10」(커밋 제목 꼴, HYO-208)
# 「Python 3.10 support will be dropped in …」 — 이름 · 버전이 앞에 오고 support가 주어인 꼴 (HYO-208, W46 langchain-core 1.7.4 · langchain 1.4.8:
# 이 꼴을 못 읽어 eol이 「확인 불가」로 남았고 detect에서 세 번 중 한 번 초안이 됐다)
_SUPPORT_GONE = r"\s+support\s+(?:will\s+be|is\s+being|has\s+been|have\s+been|was|is)\s+(?:dropped|removed|ended|discontinued)"
RUNTIMES = ("python", "node")   # 언어 런타임 — 「이 버전 지원 중단」뿐인 breaking을 우리 버전과 견줄 때 보는 이름 (HYO-202)
_HEAD = re.compile(r"^\s{0,3}(?:(#{1,6})\s*(.+?)\s*#*|(?:\*\*|__)(?P<b>[^*_].*?)(?:\*\*|__):?)\s*$")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_OTHER_BREAK = re.compile(r"\b(?:remov|renam|replac|chang|deprecat|delet|migrat|no longer|must\b|instead)\w*", re.I)   # 런타임 문장을 뺀 나머지에 다른 변경 말이 있나
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


def _eol_matches(text, names):
    """글 → [(기술 이름, re.Match)] — 이름 붙은 지원 종료 문장 (주어 꼴 · 목적어 꼴). group(1) = 버전 · group(2) = 「이하」."""
    out = []
    for n in dict.fromkeys(norm(x) for x in names):
        rx = _name_rx(n)
        if not rx:
            continue
        nv = rx + r"[\s-]?" + _VER + _LE
        for pat in (nv + _BE, _DROP + nv, nv + _SUPPORT_GONE):
            out += [(n, m) for m in re.finditer(pat, text or "", re.I)]
    return out


def eol_targets(text, names, own):
    """글 → [(기술 이름, 버전 튜플, "eq"|"le")]. names = 견줄 기술 이름(버전을 아는 것) · own = 이 행의 기술 (이름 없는 줄 표기용)."""
    out = []
    for n, m in _eol_matches(text, list(names) + ([own] if own else [])):
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


def breaking_entries(text):
    """릴리스 노트의 Breaking 절 항목들 → [항목 글] | None (절이 없거나 비었다 — 무엇이 breaking인지 스크립트가 모른다).
    절 = 제목 줄(`## Breaking Changes` · `**Breaking changes**`)부터 같은 깊이 이하의 다음 제목 줄까지. 굵은 글씨뿐인 줄은 가장 깊은 제목으로 본다.
    항목 = 글머리표 한 개(이어지는 줄 포함) · 글머리표 없는 문단 · 절 안의 더 깊은 제목 줄."""
    entries, depth, cur = [], None, None
    for line in (text or "").splitlines():
        m = _HEAD.match(line)
        if m:
            level, title, cur = len(m.group(1) or "") or 7, m.group(2) or m.group("b"), None
            if depth is not None and level > depth:
                entries.append(title)                    # 절 안의 더 깊은 제목도 breaking 내용이다
            else:
                depth = level if re.search(r"\bbreaking\b", title, re.I) else None
            continue
        if depth is None:
            continue
        if not line.strip():
            cur = None
        elif _BULLET.match(line) or cur is None:
            entries.append(_BULLET.sub("", line, count=1).strip())
            cur = len(entries) - 1
        else:
            entries[cur] += " " + line.strip()
    return entries or None


def _plain(s):
    """견주기용 글 — 마크다운 강조 · 코드 표시(`*` · `_` · 백틱)를 빼고 공백을 하나로. 모델이 옮긴 문장이 글에 그대로 있는지 볼 때 쓴다."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", s or "")).strip().lower()


def verbatim(text, quotes):
    """모델이 옮긴 근거 문장이 **전부** 글에 그대로 있나 (HYO-208) → 문장 목록 | None. 하나라도 글에 없으면 None — 고쳐 쓴 문장으로는 판정하지 않는다."""
    body = _plain(text)
    qs = [q for q in (quotes or []) if isinstance(q, str) and _plain(q)]
    return qs if qs and all(_plain(q) in body for q in qs) else None


def _basis_entries(text, basis):
    """모델이 옮긴 근거 {quotes, more} → 항목으로 볼 문장들 | None. 그 밖의 이유가 더 있다고 했으면(more) 문장이 맞아도 보지 않는다."""
    return None if basis.get("more") else verbatim(text, basis.get("quotes"))


def needs_basis(tech, text, ours_of):
    """breaking의 근거 문장을 모델에게 물어볼 만한가 (HYO-208) — Breaking 절이 없는 글이고(절이 있으면 스크립트가 읽는다), 런타임 지원 중단 문장이 있고,
    그 문장만 근거라면 뺄 수 있는(이 기술을 쓰는 repo 전부가 더 새 줄) 때만. 답이 판정을 바꿀 수 없는 소식에는 묻지 않는다."""
    if norm(tech) in RUNTIMES or breaking_entries(text):
        return False
    ms = _eol_matches(text or "", RUNTIMES)
    repos = ours_of.repos(tech)
    return bool(ms) and bool(repos) and all(_newer(ours_of, n, m, repos)[0] for n, m in ms)


def _newer(ours_of, name, m, repos):
    """런타임 지원 중단 문장 하나 → (이 기술을 쓰는 repo 전부가 그 버전보다 새 줄인가, repo별 우리 버전)."""
    ann = vtuple(m.group(1))
    by_repo = {r: ours_of.in_repo(name, r) for r in repos}
    ok = bool(by_repo) and all(vs and all((v := vtuple(x)) and len(v) >= len(ann) and v[:len(ann)] > ann for x in vs) for vs in by_repo.values())
    return ok, by_repo


_FLOOR = r"\b(?:minimum|at\s+least|requires?|upgrade\s+to)\b[^.;\n]{0,50}?"     # 「The minimum supported version is now Python 3.11」 · 「Please upgrade to Python 3.11 or later」


def _runtime_floors(text):
    """글 → [(런타임 이름, re.Match)] — 최소 버전을 말하는 구절. group(1) = 버전."""
    return [(n, m) for n in RUNTIMES for m in re.finditer(_FLOOR + _name_rx(n) + r"[\s-]?(?:>=\s*)?" + _VER, text or "", re.I)]


def _runtime_bare(text):
    """글 → [(런타임 이름, re.Match)] — 런타임 이름 + 버전만 적힌 말. group(1) = 버전."""
    return [(n, m) for n in RUNTIMES for m in re.finditer(_name_rx(n) + r"[\s-]?" + _VER, text or "", re.I)]


def _only_gone(bare, gone):
    """버전만 적힌 말이 전부 이 글이 지원을 끊는다고 한 (런타임, 버전)인가 — 다른 버전 이야기(「adds support for Python 3.15」)는 런타임 지원 중단의 곁문장이 아니다."""
    return all((n, vtuple(m.group(1))) in gone for n, m in bare)


def _at_least(ours_of, name, m, repos):
    """최소 버전 구절 하나 → 이 기술을 쓰는 repo 전부의 런타임이 그 버전 이상인가 (모르면 False)."""
    floor = vtuple(m.group(1))
    by_repo = {r: ours_of.in_repo(name, r) for r in repos}
    return bool(by_repo) and all(vs and all((v := vtuple(x)) and len(v) >= len(floor) and v[:len(floor)] >= floor for x in vs) for vs in by_repo.values())


def _runtime_only(tech, text, ours_of, basis=None):
    """breaking이 「런타임 버전 지원 중단」뿐이고 우리는 더 새 줄인가 (HYO-202) → (verdict "other"|"unknown", 근거 목록, 무엇으로 봤나) | None(볼 것이 없다).
    other = Breaking 절의 항목이 전부 런타임 지원 중단 문장이고, 이 기술을 쓰는 대상 repo 전부의 런타임 버전을 알며 모두 그 버전보다 새 줄이다.
    하나라도 모자라면 unknown — 남긴다. 절이 없는 글(첫 문장에만 적힌 꼴 — W47 langchain 1.5.0 · langchain-core 1.8.0)은 무엇이 breaking인지 스크립트가 모른다.
    그때는 basis(모델이 글에서 그대로 옮긴 breaking 근거 문장 {quotes, more} — HYO-208)를 항목으로 본다: 문장이 전부 글에 그대로 있고(verbatim),
    모델이 그 밖의 이유가 더 있다고 하지 않았을 때만. 판정은 여전히 스크립트가 한다 — 모델은 문장만 옮긴다."""
    if norm(tech) in RUNTIMES:
        return None
    entries, via = breaking_entries(text), "section"
    if not entries and basis is not None:
        entries, via = _basis_entries(text, basis), "quotes"
        if not entries:      # 다른 이유가 더 있다 · 문장을 못 옮겼다 · 고쳐 썼다 → 모른다(남긴다). 물어본 기록은 남긴다
            return "unknown", [], via
    if not entries:
        return None
    found = [(e, _eol_matches(e, RUNTIMES)) for e in entries]
    if not any(ms for _, ms in found):
        return ("unknown", [], via) if via == "quotes" else None
    repos, ev, ok = ours_of.repos(tech), [], bool(ours_of.repos(tech))
    gone = {(n, vtuple(m.group(1))) for _, ms in found for n, m in ms}       # 이 글이 지원을 끊는다고 한 (런타임, 버전)
    for entry, ms in found:
        rest = entry
        for _, m in ms:
            rest = rest.replace(m.group(0), " ")
        # 지원 중단 문장의 곁문장 (HYO-208 — 모델이 근거로 같이 옮긴다): 「최소 지원 버전은 이제 Python 3.11」 · 「Python 3.10 사용자는 1.7.x를 계속 받는다」.
        # 하한은 우리가 그 이상일 때만, 버전만 적힌 말은 그 버전이 이 글이 끊는다고 한 버전일 때만 런타임 문장으로 본다
        floors = _runtime_floors(rest)
        for _, m in floors:
            rest = rest.replace(m.group(0), " ")
        bare = _runtime_bare(rest)
        for _, m in bare:
            rest = rest.replace(m.group(0), " ")
        if not (ms or floors or bare) or _OTHER_BREAK.search(rest):          # 런타임 문장이 아닌 항목 · 한 항목에 다른 변경이 같이 적혔다
            ok = False
        if not _only_gone(bare, gone) or not all(_at_least(ours_of, n, m, repos) for n, m in floors):
            ok = False
        for n, m in ms:
            ann = vtuple(m.group(1))
            newer, by_repo = _newer(ours_of, n, m, repos)
            row = {"technology": n, "announced": ".".join(map(str, ann)), "op": "le" if m.group(2) else "eq",
                   "ours": list(dict.fromkeys(x for vs in by_repo.values() for x in vs)), "match": False if newer else None, "repos": by_repo}
            if row not in ev:
                ev.append(row)
            ok = ok and newer
    return ("other" if ok else "unknown"), ev, via


def narrow(labels, tech, tiers, item, ours_of, basis=None):
    """한 기술의 변화 라벨 → (정리한 라벨, 기록 목록). 기록 = [{change, verdict, ...}] — 무엇을 왜 뺐나/남겼나.
    ours_of(name) → 우리 버전 문자열 목록 · ours_of.names() → 버전을 아는 기술 이름들.
    basis = 모델이 글에서 그대로 옮긴 breaking 근거 문장 {quotes, more} | None (HYO-208 — Breaking 절이 없는 글에서만 쓴다)."""
    text = (item.get("title") or "") + "\n" + (item.get("text") or "")
    out, notes = [], []
    for c in labels:
        if c == "breaking":
            res = _runtime_only(tech, item.get("text") or "", ours_of, basis)
            if res:
                notes.append({"change": c, "verdict": res[0], "runtime_only": True, "evidence": res[1],
                              **({"basis": {"quotes": list(basis.get("quotes") or []), "more": bool(basis.get("more"))}} if res[2] == "quotes" else {})})
                if res[0] == "other":
                    continue
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
        self.rows = {norm(k): [r for r in v if r.get("version") and r.get("repo")] for k, v in (versions or {}).items()}
        self.human = {norm(k): list(dict.fromkeys(v)) for k, v in (human or {}).items() if v}

    def repos(self, name):
        """이 기술의 버전이 적힌 대상 repo 이름들 — 어느 repo가 쓰는지 모르면 [] (HYO-202)."""
        return list(dict.fromkeys(r["repo"] for r in self.rows.get(norm(name), [])))

    def in_repo(self, name, repo):
        """그 repo의 이 기술 버전들. 사람이 적은 버전은 repo를 가리지 않고 이긴다."""
        k = norm(name)
        return self.human.get(k) or list(dict.fromkeys(r["version"] for r in self.rows.get(k, []) if r["repo"] == repo))

    def __call__(self, name):
        k = norm(name)
        return self.human.get(k) or self.auto.get(k) or []

    def names(self):
        return list(dict.fromkeys(list(self.human) + list(self.auto)))

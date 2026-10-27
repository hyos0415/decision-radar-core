"""HITL 상태 — 항목 별도 파일 · write-ahead 쓰기 · 재개 검사 · edit 검사 (설계 docs/design/hitl-state.md, HYO-44).

원칙은 하나다: **ADR 파일이 이긴다** (결정 1). 제품 쪽 상태(checkpoint · 별도 파일)가 ADR 파일과 어긋나면 파일을 믿고,
제품 쪽을 고치거나 사람에게 보인다 — 사람이 고쳤을지 모르는 파일을 덮지 않는다.

  결정 2  `<ADR repo 루트>/.radar/items/<item-id>.yaml` — 항목 하나당 파일 하나. `status` 칸이 없다 (R8)
  결정 3  재개: 별도 파일 읽기 → 쓰다 만 것 회복 → 지문 비교 → 다르면 차이를 보여 다시 interrupt (계속 · 다시 판정 · 버림)
  결정 4  쓰기: 번호 → 별도 파일에 의도 → ADR 파일 + validate → 완료 (write-ahead). 각 단계 뒤 파일을 닫는다
  결정 5  thread_id = item-id (capture.py)
  결정 6  edit: 고친 초안도 validate를 지난다 — 실패하면 쓰지 않고 오류를 돌려준다. status·번호는 못 바꾼다
  결정 7  지문 A′ = docs/adr/*.md 전체(기록용 칸 제외) + 대상 repo CLAUDE.md의 스택 절. `RADAR_FINGERPRINT=related`로 B′(관련만)로 끈다
  결정 8  내보내기 = 승인하면 main에 바로 커밋·push (export.py). detect 초안은 Inbox 검토(`review_item`) 뒤에 내보낸다. eval·dry-run은 no-op

지문은 `Fingerprint` 인터페이스 뒤에 있다. 기본값은 A′(`all`)이고, 원래의 (A) `repo_hash`·(B) `related_match`는 기록용 칸에 반응해서
기각됐다 (결정 7) — 이미 저장된 지문을 읽는 호환용으로만 남겼다.
"""

import abc
import copy
import dataclasses
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile

import yaml
from pydantic import ValidationError

from common import ops
from common.core import radar
from common.schemas import AdrDraft

from .export import Exporter, GitExporter, LocalExporter

ITEM_KINDS = ("capture", "detect", "experiment")
ITEM_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
# edit으로 바꿀 수 없는 칸 (결정 6 / R8) — 초안 스키마에 칸이 없다. 넣어 보내면 조용히 버리지 않고 오류로 돌려준다
EDIT_FORBIDDEN = ("status", "id", "number", "adr", "adr_id")


class HitlError(Exception):
    pass


class ItemInvalid(HitlError):
    def __init__(self, path, errors):
        super().__init__(f"별도 파일 형식 오류 {path}: {errors}")
        self.path, self.errors = path, errors


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now(ctx):
    return ctx.now or dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _quiet(ctx):
    """읽기 전용 호출(지문·검증용)은 transcript에 도구 호출로 남기지 않는다 — R12 호출 수에 섞이지 않게."""
    return dataclasses.replace(ctx, events=[])


# ── 별도 파일 (결정 2) ───────────────────────────────────────────────

def repo_root(ctx):
    """ADR repo 루트 — RADAR_ADR_DIR(…/docs/adr)의 두 단계 위."""
    return os.path.dirname(os.path.dirname(os.path.abspath(ctx.adr_dir)))


def items_dir(ctx):
    return os.path.join(repo_root(ctx), ".radar", "items")


def item_path(ctx, item_id):
    if not ITEM_ID.match(item_id or ""):
        raise HitlError(f"item-id 형식이 아니다: {item_id!r}")
    return os.path.join(items_dir(ctx), item_id + ".yaml")


def validate_item(d):
    """별도 파일 형식 검사 — 제품 코드에서 한다 (Q5 미정: radar.py로 옮길지는 사용자 결정). 오류 목록."""
    errs = []
    if not isinstance(d, dict):
        return ["최상위가 매핑이 아니다"]
    if "status" in d:
        errs.append("status 칸이 있다 — status는 ADR 파일에 있고 사람만 바꾼다 (R8). 별도 파일은 status를 흉내 내지 않는다")
    for k in ("id", "kind", "lifecycle", "created", "updated", "history"):
        if k not in d:
            errs.append(f"{k} 칸이 없다")
    if d.get("kind") not in ITEM_KINDS:
        errs.append(f"kind는 {ITEM_KINDS} 중 하나: {d.get('kind')!r}")
    if not isinstance(d.get("history", []), list):
        errs.append("history가 목록이 아니다")
    wi = d.get("write_intent")
    if wi is not None:
        if not isinstance(wi, dict):
            errs.append("write_intent가 매핑이 아니다")
        else:
            for k in ("adr", "path", "content_hash", "state"):
                if k not in wi:
                    errs.append(f"write_intent.{k} 칸이 없다")
            if wi.get("state") not in ("intended", "done"):
                errs.append(f"write_intent.state는 intended|done: {wi.get('state')!r}")
    return errs


def read_item(ctx, item_id):
    """없으면 None. 있는데 형식이 틀리면 ItemInvalid — 사람이 손으로 고친 파일일 수 있다."""
    p = item_path(ctx, item_id)
    if not os.path.exists(p):
        return None
    try:
        d = yaml.safe_load(open(p, encoding="utf-8").read())
    except yaml.YAMLError as e:
        raise ItemInvalid(p, [f"YAML을 읽지 못함: {e}"])
    errs = validate_item(d)
    if errs:
        raise ItemInvalid(p, errs)
    return d


def save_item(ctx, item):
    """원자적으로 한 번에 쓰고 닫는다 (임시 파일 → replace). 형식이 틀린 것은 쓰지 않는다."""
    errs = validate_item(item)
    if errs:
        raise ItemInvalid(item.get("id"), errs)
    item["updated"] = _now(ctx)
    p = item_path(ctx, item["id"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        yaml.safe_dump(item, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, p)


def new_item(ctx, item_id, kind, lifecycle):
    return {"id": item_id, "kind": kind, "lifecycle": lifecycle, "adr": None, "created": _now(ctx), "updated": _now(ctx),
            "fingerprint": None, "write_intent": None, "history": []}


def new_item_id(ctx, kind, label, taken=()):
    """`<날짜>-<kind>-<label>` — 이미 있는 항목(파일이나 대기 중인 thread)과 겹치면 -2, -3…. 덮지 않는다 (S2)."""
    base = f"{_now(ctx)[:10]}-{kind}-{ops.slug(label)}"
    n, cand = 1, base
    while os.path.exists(item_path(ctx, cand)) or cand in taken:
        n += 1
        cand = f"{base}-{n}"
    return cand


def _event(ctx, **kw):
    return {"at": _now(ctx), **kw}


# ── 지문 (결정 3 · 7) ────────────────────────────────────────────────

_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _adr_files(ctx, world=True):
    """docs/adr/*.md. world=True면 「아직 내보내지 않은 로컬 초안」(검토 대기·내보내기 보류 항목의 ADR)을 뺀다 — 지문이 보는 세상은
    main에 있는 ADR이다. 그 초안들은 사람이 검토하면 들어오거나 지워지는 것이라 세상의 변화가 아니다 (아니면 detect 초안 하나를 reject할 때마다
    나머지 초안이 헛경보를 낸다). 내보내기를 안 쓰는 흐름(eval·dry-run)에는 보류 항목이 없어 달라지는 것이 없다."""
    skip = pending_names(ctx) if world else set()
    return sorted(p for p in glob.glob(os.path.join(os.path.abspath(ctx.adr_dir), "*.md")) if os.path.basename(p) not in skip)


def _file_meta(path):
    text = open(path, "rb").read()
    try:
        m = _FM.match(text.decode("utf-8"))
        fm = (yaml.safe_load(m.group(1)) or {}) if m else {}
    except (UnicodeDecodeError, yaml.YAMLError):
        fm = {}
    return hashlib.sha256(text).hexdigest(), {"status": fm.get("status"), "technology": fm.get("technology")}


class Fingerprint(abc.ABC):
    """멈출 때 근거로 삼은 세상의 요약. 계산 · 비교 · 차이 요약 세 메서드만 안다 — 무엇을 넣을지(Q2)는 구현의 몫이다.

    scope는 항목이 가리키는 것({technology, use, affected, related}) — 구현이 「관련」을 정의하는 데 쓴다.
    """
    name = "abstract"

    @abc.abstractmethod
    def compute(self, ctx, scope):
        """JSON으로 저장되는 dict. 반드시 {"impl": self.name}를 넣는다."""

    @abc.abstractmethod
    def same(self, old, new):
        """같은가 (다시 묻지 않아도 되는가)."""

    @abc.abstractmethod
    def diff(self, old, new):
        """무엇이 바뀌었나 — 사람이 읽는 줄 목록."""


def _file_diff(old, new, names=None):
    of, nf = old.get("files", {}), new.get("files", {})
    om, nm = old.get("meta", {}), new.get("meta", {})
    lines = []
    for n in sorted(set(of) | set(nf)):
        if names is not None and n not in names:
            continue
        if n not in of:
            lines.append(f"새 ADR {n} (technology: {nm.get(n, {}).get('technology')}, status: {nm.get(n, {}).get('status')})")
        elif n not in nf:
            lines.append(f"ADR {n}이 사라졌다")
        elif of[n] != nf[n]:
            a, b = om.get(n, {}).get("status"), nm.get(n, {}).get("status")
            lines.append(f"ADR {n}의 status가 {a} → {b}로 바뀌었다" if a != b else f"ADR {n}의 내용이 바뀌었다")
    return lines


class RepoHashFingerprint(Fingerprint):
    """(A) ADR repo 전체 해시 — docs/adr/*.md 파일 해시 전부. **기각 (결정 7)** — 기록용 칸(revisit_last_checked)에도 반응해서 매주 헛경보를 낸다.
    이미 저장된 지문의 비교를 위해서만 남겼다 (check_resume은 저장된 impl 이름으로 구현을 고른다)."""
    name = "repo_hash"

    def compute(self, ctx, scope):
        files, meta = {}, {}
        for p in _adr_files(ctx):
            files[os.path.basename(p)], meta[os.path.basename(p)] = _file_meta(p)
        return {"impl": self.name, "files": files, "meta": meta}

    def same(self, old, new):
        return old.get("impl") == new.get("impl") and old.get("files") == new.get("files")

    def diff(self, old, new):
        return _file_diff(old, new)


class RelatedMatchFingerprint(Fingerprint):
    """(B) 관련 ADR 파일 해시 + 같은 기술 `radar.py match` 결과. **기각 (결정 7)** — (A)와 같은 이유. B′(`RelatedFingerprint`)가 이어받았다.
    관련 = 초안이 적은 관련 ADR + match가 돌려준 exact·overlap·related."""
    name = "related_match"

    def _match(self, ctx, scope):
        c = _quiet(ctx)
        exact, overlap, related = set(), set(), set()
        for a in scope.get("affected") or []:
            m = radar(c, "match", c.adr_dir, "--technology", scope["technology"], "--repo", a["repo"],
                      "--component", a["component"], "--use", scope.get("use") or "")
            exact |= set(m["exact"])
            overlap |= set(m["overlap"])
            related |= {r["id"] for r in m["related"]}
        # match는 radar.py가 폴더를 통째로 읽어 아직 내보내지 않은 로컬 초안(자기 자신 포함)도 돌려준다 — 파일 해시 쪽(_adr_files)처럼 세상으로 보지 않는다.
        # 안 그러면 B′에서는 내보내기를 켜자마자 자기 초안이 match에 걸려 매번 「낡음」이다
        own = {n[:4] for n in pending_names(ctx)}
        return {"exact": sorted(exact - own), "overlap": sorted(overlap - own), "related": sorted(related - own)}

    def compute(self, ctx, scope):
        match = self._match(ctx, scope)
        ids = set(scope.get("related") or []) | set(match["exact"]) | set(match["overlap"]) | set(match["related"])
        files, meta = {}, {}
        for p in _adr_files(ctx):
            n = os.path.basename(p)
            if n[:4] in ids:
                files[n], meta[n] = _file_meta(p)
        return {"impl": self.name, "files": files, "meta": meta, "match": match}

    def same(self, old, new):
        return old.get("impl") == new.get("impl") and old.get("files") == new.get("files") and old.get("match") == new.get("match")

    def diff(self, old, new):
        lines = _file_diff(old, new)
        for k, label in (("exact", "3중 키가 같은 ADR"), ("overlap", "같은 기술·component ADR"), ("related", "관련 ADR")):
            a, b = set(old.get("match", {}).get(k, [])), set(new.get("match", {}).get(k, []))
            if a != b:
                lines.append(f"match {label}: {sorted(a)} → {sorted(b)}")
        return lines


# ── 기록용 칸 (결정 7) ────────────────────────────────────────────────
# 지문에서 뺀다 — 기계가 쓰는 칸이 바뀐 것을 「세상이 바뀌었다」로 읽으면 매주 월요일 revisit routine이 대기 중인 모든 항목을
# 의미 없이 다시 승인하게 만들고, 사람은 「계속」을 습관적으로 누르게 된다 (설계 §4-1).
#
# 스킬·radar.py가 자동으로 쓰는 칸을 전부 찾은 결과 (2026-10-06, `.claude/skills/jhs-decisions/`):
#   - `revisit_last_checked` (최상위 칸 전체) — radar.py `write_updates`(scripts/radar.py:367-385)만 쓴다. `cmd_apply`(:361)·`cmd_decide --apply`(:357)가
#     부르고, 다른 칸은 건드리지 않는다(:380 이 칸만 갈아 끼우고 :381 `assert status 불변`, R8). schema.md:29·:49가 「재검토 루프만 쓴다. 사람은 손대지
#     않아도 된다」·「radar.py apply만 쓴다」, SKILL.md:22 「revisit_last_checked는 radar.py만 쓴다」.
#   - 그 칸의 항목 안 `checked_at`·`result`·`reason`·`evidence_urls`·`condition_id`·`last_determined` — radar.py:320-322(`cmd_decide`의 entry)·:343-344
#     (결과 누락 채움). 칸 전체를 빼므로 따로 빼지 않아도 되지만, `last_determined`는 어디에 나타나든 뺀다 (재검토 조건 안에 들어가도 기록용 — 방어).
#   - 그 밖에 스킬이 ADR 파일에 자동으로 쓰는 칸은 없다 (radar.py의 쓰기는 write_updates와 next-id 예약 파일뿐; 초안은 ops.render가 새 파일로 만든다).
# 사람이 쓰는 칸은 넣는다: status · technology · use · affected · evidence · eval_assets · revisit_when(조건 본문) · date · 본문 텍스트.
# `date`는 초안을 만들 때 한 번 쓰고(ops.render) 그 뒤 기계가 바꾸지 않는다 — 사람 칸으로 본다.
# 새 기록용 칸이 생기면 스킬 schema.md와 함께 이 목록을 본다 — 빠뜨리면 그 칸의 변화가 매번 「세상이 바뀌었다」가 된다.
MACHINE_FIELDS = ("revisit_last_checked",)
MACHINE_KEYS_ANYWHERE = ("last_determined",)


def _strip_machine(obj, top=True):
    """기록용 칸을 뺀 사본. 최상위에서는 MACHINE_FIELDS, 어디서든 MACHINE_KEYS_ANYWHERE."""
    if isinstance(obj, dict):
        return {k: _strip_machine(v, False) for k, v in obj.items()
                if k not in MACHINE_KEYS_ANYWHERE and not (top and k in MACHINE_FIELDS)}  # MUT:machine
    if isinstance(obj, list):
        return [_strip_machine(v, False) for v in obj]
    return obj


def _canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _h12(obj):
    return sha(_canon(obj))[:12]


def _scalar(v):
    return v is None or isinstance(v, (str, int, float, bool)) or hasattr(v, "isoformat")


def adr_digest(path):
    """ADR 한 파일의 지문 조각 — 기록용 칸을 뺀 프론트매터와 본문을 정규화해 해시한다.
    프론트매터는 파싱한 값으로 비교한다 (radar.py가 `yaml.safe_dump`로 다시 쓰면 줄 모양이 바뀌므로 글자 비교는 헛경보다).
    {"hash", "fm": {칸: 값(스칼라) | 해시(그 밖)}, "cond": {조건 id: 해시}, "body": 해시}."""
    text = open(path, "rb").read().decode("utf-8", "replace").replace("\r\n", "\n")
    m = _FM.match(text)
    try:
        fm = (yaml.safe_load(m.group(1)) or {}) if m else None
    except yaml.YAMLError:
        fm = None
    if not isinstance(fm, dict):  # 프론트매터가 없거나 깨졌다 — 파일 전체를 본문으로 본다
        return {"hash": sha(text), "fm": {}, "cond": {}, "body": sha(text)}
    fm = _strip_machine(fm)
    body = text[m.end():]
    detail = {k: (str(v) if _scalar(v) else _h12(v)) for k, v in fm.items() if k != "revisit_when"}
    cond = {}
    for i, c in enumerate(fm.get("revisit_when") or []):
        cond[str(c.get("id", i)) if isinstance(c, dict) else str(i)] = _h12(c)
    return {"hash": sha(_canon({"fm": fm, "body": body})), "fm": detail, "cond": cond, "body": sha(body)[:12]}


def _adr_part(ctx, names=None):
    files, detail = {}, {}
    for p in _adr_files(ctx):
        n = os.path.basename(p)
        if names is None or n in names:
            d = adr_digest(p)
            files[n], detail[n] = d["hash"], {k: d[k] for k in ("fm", "cond", "body")}
    return files, detail


def _adr_diff(old, new, names=None):
    of, nf = old.get("files", {}), new.get("files", {})
    od, nd = old.get("detail", {}), new.get("detail", {})
    lines = []
    for n in sorted(set(of) | set(nf)):
        if names is not None and n not in names:
            continue
        if n not in of:
            fm = nd.get(n, {}).get("fm", {})
            lines.append(f"새 ADR {n} (technology: {fm.get('technology')}, status: {fm.get('status')})")
        elif n not in nf:
            lines.append(f"ADR {n}이 사라졌다")
        elif of[n] != nf[n]:
            a, b = od.get(n, {}), nd.get(n, {})
            sub = []
            for k in sorted(set(a.get("fm", {})) | set(b.get("fm", {}))):
                x, y = a.get("fm", {}).get(k), b.get("fm", {}).get(k)
                if x != y:
                    sub.append(f"{k}: {x} → {y}" if (k in ("status", "technology", "use", "date") and x is not None and y is not None) else f"{k} 칸이 바뀌었다")
            ac, bc = a.get("cond", {}), b.get("cond", {})
            for c in sorted(set(ac) | set(bc)):
                if c not in ac:
                    sub.append(f"재검토 조건 {c} 추가")
                elif c not in bc:
                    sub.append(f"재검토 조건 {c} 삭제")
                elif ac[c] != bc[c]:
                    sub.append(f"재검토 조건 {c} 바뀜")
            if a.get("body") != b.get("body"):
                sub.append("본문이 바뀌었다")
            lines.append(f"ADR {n[:4]} ({n}) " + "; ".join(sub or ["내용이 바뀌었다"]))
    return lines


# ── 대상 repo CLAUDE.md의 스택 절 (결정 7) ───────────────────────────────
# 초안의 `affected`가 「의존성 → component」 매핑에서 나오므로, 대기 중에 대상 repo 의존성이 바뀌면 초안이 낡는다.
# 절 찾는 규칙은 collector/sources/vendor.py `services_from_claude_md`·github.py `dependencies_from_claude_md`와 같다 —
# 제목에 「외부 모델」 / 「의존성」이 들어간 절. 다만 그 함수들은 첫 열 이름만 돌려줘 component 열(매핑)을 버리고, product에서 collector를
# import하지 않으므로 같은 규칙으로 행 전체를 읽는 이 함수를 따로 둔다 (같은 이름이 나오는지는 test_hitl이 collector 함수와 맞춰 본다).

STACK_SECTIONS = (("deps", "의존성"), ("services", "외부 모델"))
_HEAD = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def stack_rows(text):
    """{"deps": [행…], "services": [행…]} — 절 안의 비지 않은 줄(공백 정리, 표 구분선 제외). 절이 없으면 빈 목록."""
    out = {k: [] for k, _ in STACK_SECTIONS}
    cur, level = None, 0
    for line in text.replace("\r\n", "\n").split("\n"):
        h = _HEAD.match(line)
        if h:
            lv, title = len(h.group(1)), h.group(2)
            if cur is not None and lv <= level:
                cur = None
            if cur is None:
                cur = next((k for k, mark in STACK_SECTIONS if mark in title), None)
                level = lv if cur else 0
            continue
        if cur is None:
            continue
        row = re.sub(r"\s+", " ", line.strip())
        if row and not set(row) <= set("|-: "):
            out[cur].append(row)
    return out


def _stack_part(ctx, repos=None):
    """{repo: {"deps": […], "services": […]} | {"missing": True}} — repos가 None이면 대상 repo 전부."""
    out = {}
    for repo in sorted(ctx.targets):
        if repos is not None and repo not in repos:
            continue
        p = os.path.join(ctx.targets[repo], "CLAUDE.md")
        out[repo] = stack_rows(open(p, encoding="utf-8").read()) if os.path.exists(p) else {"missing": True}
    return out


def _stack_diff(old, new):
    lines = []
    for repo in sorted(set(old) | set(new)):
        a, b = old.get(repo), new.get(repo)
        if a == b:
            continue
        if a is None or b is None:
            lines.append(f"대상 repo {repo}가 {'목록에 새로 들어왔다' if a is None else '목록에서 빠졌다'}")
            continue
        if a.get("missing") or b.get("missing"):
            lines.append(f"대상 repo {repo}의 CLAUDE.md가 {'생겼다' if a.get('missing') else '사라졌다'}")
            continue
        for k, label in (("deps", "의존성 → component"), ("services", "외부 모델·서비스")):
            ra, rb = a.get(k, []), b.get(k, [])
            if ra != rb:
                add, rem = [r for r in rb if r not in ra], [r for r in ra if r not in rb]
                parts = [f"추가 {add}" if add else "", f"삭제 {rem}" if rem else "", "" if add or rem else "순서만 바뀜"]
                lines.append(f"{repo} CLAUDE.md 「{label}」: " + "; ".join(x for x in parts if x))
    return lines


class AllFingerprint(Fingerprint):
    """A′ (결정 7, 기본) — docs/adr/*.md 전체(기록용 칸 제외) + 대상 repo CLAUDE.md의 스택 절.
    의미 있는 ADR 변화는 드물어서 무관한 변화에 다시 묻는 일이 드물고, 「관련」을 정의하지 않으니 놓치는 것이 없다.
    기록용 칸(revisit_last_checked 등)만 바뀐 것은 같다고 본다."""
    name = "all"

    def compute(self, ctx, scope):
        files, detail = _adr_part(ctx)
        return {"impl": self.name, "files": files, "detail": detail, "stack": _stack_part(ctx)}

    def same(self, old, new):
        return (old.get("impl") == new.get("impl") and old.get("files") == new.get("files")
                and old.get("stack") == new.get("stack"))  # MUT:stack

    def diff(self, old, new):
        return _adr_diff(old, new) + _stack_diff(old.get("stack", {}), new.get("stack", {}))


class RelatedFingerprint(RelatedMatchFingerprint):
    """B′ (결정 7의 끄는 스위치, `RADAR_FINGERPRINT=related`) — 관련 ADR(초안이 적은 것 + `radar.py match`) 해시 + match 결과 +
    초안이 가리키는 대상 repo의 스택 절. (B)와 같되 기록용 칸을 뺀다. 필요한 것만 다시 묻지만 「관련」의 정의(문자열 동일성)에 기댄다.
    스택 절은 (B)에 없던 것이다 — 초안의 affected가 거기서 나왔는데 B′가 그 변화를 못 보면 낡은 매핑을 말없이 저장한다."""
    name = "related"

    def compute(self, ctx, scope):
        match = self._match(ctx, scope)
        ids = set(scope.get("related") or []) | set(match["exact"]) | set(match["overlap"]) | set(match["related"])
        files, detail = _adr_part(ctx, {os.path.basename(p) for p in _adr_files(ctx) if os.path.basename(p)[:4] in ids})
        repos = {a["repo"] for a in scope.get("affected") or []}
        return {"impl": self.name, "files": files, "detail": detail, "match": match, "stack": _stack_part(ctx, repos)}

    def same(self, old, new):
        return (old.get("impl") == new.get("impl") and old.get("files") == new.get("files") and old.get("match") == new.get("match")
                and old.get("stack") == new.get("stack"))

    def diff(self, old, new):
        lines = _adr_diff(old, new)
        for k, label in (("exact", "3중 키가 같은 ADR"), ("overlap", "같은 기술·component ADR"), ("related", "관련 ADR")):
            a, b = set(old.get("match", {}).get(k, [])), set(new.get("match", {}).get(k, []))
            if a != b:
                lines.append(f"match {label}: {sorted(a)} → {sorted(b)}")
        return lines + _stack_diff(old.get("stack", {}), new.get("stack", {}))


FINGERPRINTS = {f.name: f for f in (AllFingerprint(), RelatedFingerprint(), RepoHashFingerprint(), RelatedMatchFingerprint())}
DEFAULT_FINGERPRINT = "all"  # 결정 7. 바꾸려면 환경변수 RADAR_FINGERPRINT=all|related (거슬리면 related로 끈다)


def fingerprint_for(ctx=None, name=None):
    """지문 구현을 고른다 — 이름이 있으면 그것(저장된 지문의 impl), 없으면 환경변수 RADAR_FINGERPRINT(all|related), 없으면 기본 all(A′).
    「많이 알려 주다가 거슬리면 끈다」(사용자): RADAR_FINGERPRINT=related로 B′(관련만)로 바꾼다."""
    name = name or ((ctx.env.get("RADAR_FINGERPRINT") if ctx is not None else None) or DEFAULT_FINGERPRINT)
    if name not in FINGERPRINTS:
        raise HitlError(f"모르는 지문 구현 {name!r} — {sorted(FINGERPRINTS)}")
    return FINGERPRINTS[name]


def scope_of(draft):
    d = draft if isinstance(draft, dict) else draft.model_dump()
    return {"technology": d.get("technology"), "use": d.get("use"), "affected": d.get("affected") or [], "related": d.get("related") or []}


# ── 멈춘 항목의 지문 보관 (승인 전: R9 때문에 ADR repo에 파일을 둘 수 없다) ─────────

class StateStore:
    """대기 중인 thread의 지문·범위 — checkpoint와 같은 SQLite 파일의 별도 표. ADR repo가 아니라서 R9(승인 전 쓰기 0)에 안 걸린다.
    checkpoint thread와 같이 지워진다 (끝난 항목의 진행용 상태는 남기지 않는다 — §6)."""

    def __init__(self, path):
        self.path = path

    def _run(self, sql, args=()):
        con = sqlite3.connect(self.path)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS radar_hitl (thread_id TEXT PRIMARY KEY, fingerprint TEXT, scope TEXT)")
            cur = con.execute(sql, args)
            rows = cur.fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def put(self, thread_id, fingerprint, scope):
        import json
        self._run("INSERT OR REPLACE INTO radar_hitl VALUES (?,?,?)", (thread_id, json.dumps(fingerprint), json.dumps(scope, ensure_ascii=False)))

    def get(self, thread_id):
        import json
        rows = self._run("SELECT fingerprint, scope FROM radar_hitl WHERE thread_id=?", (thread_id,))
        return (json.loads(rows[0][0]), json.loads(rows[0][1])) if rows else (None, None)

    def threads(self):
        return [r[0] for r in self._run("SELECT thread_id FROM radar_hitl ORDER BY thread_id")]

    def delete(self, thread_id):
        self._run("DELETE FROM radar_hitl WHERE thread_id=?", (thread_id,))


# ── 내보내기 (결정 8, §5-2) ──────────────────────────────────────────
# git 기계장치는 export.py. 여기는 「무엇을 언제 내보내나」다.
#   capture  승인(approve·edit) → write-ahead로 로컬에 쓴 뒤 바로 내보낸다 (`_complete` → `export_item`)
#   detect   초안은 로컬에 쓰고 항목을 검토 대기(USER_REVIEW, export.state=review)로 둔다 → 사람이 검토(`review_item`)하면
#            approve는 내보내기, reject는 그 초안(아직 커밋 안 된 새 파일)을 지우고 항목을 닫는다
#   eval·dry-run·스텁은 늘 no-op — `exporter_for`가 두 가지(RADAR_EXPORT=git · dry-run 아님)를 모두 요구하고, GitExporter도 dry-run이면 git을 안 부른다
#
# 항목 별도 파일의 `export` 칸 (내보내기를 쓸 때만 생긴다): {state: review|pending|stale|blocked|done|closed, …}
#   review  검토 대기(detect)  pending  내보낼 차례  stale  재개 검사에서 세상이 달라졌다 — 사람의 답을 기다린다 (changes)
#   blocked 막혔다(동기화·push 실패·번호 충돌·허용 목록 밖 …) — 사람에게 보인다  done  커밋·push 끝  closed  검토에서 거부·버림
PENDING_EXPORT = ("review", "pending", "stale", "blocked")
DEFAULT_EXPORTER = Exporter()


def exporter_for(ctx):
    """기본은 no-op. `RADAR_EXPORT=git`이고 dry-run이 아닐 때만 GitExporter — 실수로 켜지는 것을 막으려 둘 다 요구한다 (eval 러너는 RADAR_DRY_RUN=1).
    RADAR_EXPORT_REMOTE·RADAR_EXPORT_BRANCH(기본 origin·main) · RADAR_GIT_NAME·RADAR_GIT_EMAIL(없으면 git 설정을 따른다)."""
    if ctx.env.get("RADAR_EXPORT") == "local":  # 데스크톱 앱의 ADR 사본 — git 없이 승인 기록만 (HYO-85). 밖으로 나가지 않아 dry-run과 같이 켜진다
        return LocalExporter(repo_root(ctx))
    if ctx.env.get("RADAR_EXPORT") == "git" and not ctx.dry_run:  # MUT:exporter_for
        return GitExporter(repo_root(ctx), dry_run=ctx.dry_run, remote=ctx.env.get("RADAR_EXPORT_REMOTE") or "origin",
                           branch=ctx.env.get("RADAR_EXPORT_BRANCH") or "main",
                           identity={"name": ctx.env.get("RADAR_GIT_NAME"), "email": ctx.env.get("RADAR_GIT_EMAIL")})
    return DEFAULT_EXPORTER


def pending_names(ctx):
    """아직 내보내지 않은 로컬 초안의 ADR 파일 이름 — 지문이 세상으로 보지 않는다 (`_adr_files`)."""
    d, out = items_dir(ctx), set()
    if not os.path.isdir(d):
        return out
    for f in os.listdir(d):
        if not f.endswith(".yaml"):
            continue
        try:
            it = yaml.safe_load(open(os.path.join(d, f), encoding="utf-8").read()) or {}
        except (yaml.YAMLError, OSError):
            continue
        if not isinstance(it, dict):
            continue
        wi = it.get("write_intent") or {}
        if (it.get("export") or {}).get("state") in PENDING_EXPORT and wi.get("path"):
            out.add(os.path.basename(wi["path"]))
    return out


# ── write-ahead 쓰기 (결정 4) ────────────────────────────────────────

def _hook(after, name):
    if after:
        after(name)  # 테스트가 단계 사이에서 죽이는 자리 (예외를 던지거나 프로세스를 끝낸다)


def _rel(ctx, path):
    return os.path.relpath(path, repo_root(ctx)).replace(os.sep, "/")


def _abs(ctx, rel):
    return os.path.join(repo_root(ctx), *rel.split("/"))


def _create_adr_file(ctx, path, content):
    """없는 파일만 만든다 ("x") — 있으면 덮지 않고 FileExistsError (결정 1)."""
    ctx.record("Write", {"file_path": path})
    with open(path, "x", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _validate(ctx, nid):
    v = radar(ctx, "validate", ctx.adr_dir, ok_codes=(0, 1))
    return next((a["errors"] for a in v["adrs"] if a["id"] == nid), ["validate에 없음"])


def _id_files(ctx, nid):
    return sorted(glob.glob(os.path.join(os.path.abspath(ctx.adr_dir), f"{nid}-*.md")))


def _file_hash(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _result(ctx, item, errs=None, recovery=None, blocked=None, export=None):
    wi = item.get("write_intent") or {}
    out = {"written": _abs(ctx, wi["path"]) if wi and not blocked else None, "id": wi.get("adr"), "validate_errors": errs or [],
           "item": item["id"], "state": wi.get("state")}
    if export is not None:
        out["export"] = export
    if recovery:
        out["recovery"] = recovery
    if blocked:
        out["blocked"] = blocked
    return out


def _complete(ctx, item, errs, final_lifecycle, exporter, event="adr_written"):
    """4. 완료를 적고, 내보내기를 쓰면 이어서 내보낸다. 내보내기 결과(없으면 None)를 돌려준다."""
    wi = item["write_intent"]
    wi["state"] = "done"
    wi.pop("content", None)  # 완료되면 본문은 ADR 파일에 있다 — 해시만 남긴다
    if errs:
        wi["validate_errors"] = list(errs)
    else:
        wi.pop("validate_errors", None)
    item["adr"] = wi["adr"]
    ex = exporter or exporter_for(ctx)
    if ex.active and (item.get("export") or {}).get("state") is None:  # 내보내기를 켠 뒤에 회복되는 항목 — 내보낼 차례로 둔다
        item["export"] = {"state": "pending"}
    exp = (item.get("export") or {}).get("state")
    deferred = exp in PENDING_EXPORT  # 내보내기가 남은 항목은 아직 끝난 것이 아니다 — 마지막 lifecycle은 커밋하는 순간에 쓴다
    ev = {"event": event, "adr": wi["adr"], "by": "graph"}
    if errs:
        ev["validate_errors"] = len(errs)
    if final_lifecycle and not deferred:
        item["lifecycle"] = final_lifecycle
        ev["to"] = final_lifecycle
    item["history"].append(_event(ctx, **ev))
    save_item(ctx, item)  # 4. 완료
    if ex.active and exp in ("pending", "stale", "blocked"):  # review는 사람의 검토를 기다린다 (detect)
        return export_item(ctx, item["id"], ex)
    return {"state": "review"} if exp == "review" else None


def write_adr(ctx, item_id, kind, draft, *, lifecycle="USER_REVIEW", final_lifecycle="DONE", by="human",
              approval=None, after=None, exporter=None, hold_export=False, run_id=None, sources=None):
    """승인된 초안을 쓴다 — 1 번호 → 2 별도 파일에 의도 → 3 ADR 파일 + validate → 4 완료. 각 단계 뒤 파일은 닫혀 있다.

    approval: {"decision": "approve"|"edit", "fingerprint": 승인 근거 지문, "draft_hash_original": edit 전 초안의 해시}.
    내보내기를 켰으면(exporter_for) 승인된 쓰기가 끝나는 대로 main에 커밋·push한다 (결정 8). hold_export=True(detect)면 쓰기까지만 하고
    항목을 검토 대기(USER_REVIEW · export.state=review)로 둔다 — 사람이 `review_item`으로 답하면 그때 내보내거나 지운다.
    run_id는 「같은 실행」의 기록이다(detect 한 번이 초안 여러 개에 같은 값을 준다) — 별도 파일에 남아, 같은 실행의 다른 항목이 내보낸 ADR을
    재개 검사에서 넘기는 근거가 된다 (`_own_exports`). 없으면(capture) 어떤 내보내기도 넘기지 않는다.
    이미 의도가 있으면 새로 쓰지 않고 회복한다 (그래프가 같은 도구 노드를 다시 돌려도 ADR이 두 개가 되지 않는다 — S3).
    """
    approval = approval or {}
    item = read_item(ctx, item_id)
    if item and item.get("write_intent"):
        if item["write_intent"]["state"] == "done":
            return _result(ctx, item, recovery={"action": "already_done"})
        rec = recover(ctx, item_id, after=after, final_lifecycle=final_lifecycle, exporter=exporter)
        return rec["result"]
    ex = exporter or exporter_for(ctx)

    nid = radar(ctx, "next-id", ctx.adr_dir)["next_id"]  # 1. 번호는 쓰는 이 순간에 정한다
    _hook(after, "number")
    content = ops.render(draft, ctx.now)
    chash = sha(content)
    path = os.path.join(os.path.abspath(ctx.adr_dir), f"{nid}-{ops.slug(draft.technology)}.md")

    item = item or new_item(ctx, item_id, kind, lifecycle)
    if run_id:
        item["run_id"] = run_id
    if sources:  # detect: 이 초안이 나온 소식(피드 항목 id) — 사람이 거부하면 다음 detect가 같은 소식을 다시 올리지 않는다 (HYO-142)
        item["sources"] = list(sources)
    if hold_export and ex.active:
        lifecycle = "USER_REVIEW"
    item["lifecycle"] = lifecycle
    item["scope"] = scope_of(draft)  # 재개 검사(지문)가 「관련」을 정의하는 데 쓴다 — 내보낼 때 다시 읽는다
    item["fingerprint"] = approval.get("fingerprint")
    if ex.active:
        if item["fingerprint"] is None:  # 승인 근거 지문이 없는 흐름(detect) — 초안을 쓰는 지금의 세상이 근거다
            item["fingerprint"] = fingerprint_for(ctx).compute(_quiet(ctx), item["scope"])
        item["export"] = {"state": "review" if hold_export else "pending"}
    item["write_intent"] = {"adr": nid, "path": _rel(ctx, path), "content_hash": chash, "state": "intended", "content": content}
    ev = {"to": lifecycle, "by": by, "decision": approval.get("decision", "approve")}
    if approval.get("decision") == "edit":  # 모델 초안을 사람이 얼마나 고쳤나 — 초안 품질 지표 (§5-1)
        ev.update(draft_hash_original=approval.get("draft_hash_original"), draft_hash_edited=chash)
    else:
        ev["draft_hash"] = chash
    item["history"].append(_event(ctx, **ev))
    save_item(ctx, item)  # 2. 의도 (MUT:intent)
    _hook(after, "intent")

    _create_adr_file(ctx, path, content)  # 3. ADR 파일
    errs = _validate(ctx, nid)
    _hook(after, "adr")

    exp = _complete(ctx, item, errs, final_lifecycle, ex)
    _hook(after, "done")
    return _result(ctx, item, errs, export=exp)


def _block(ctx, item, reason, **detail):
    """사람에게 보이는 상태로 멈춘다 — `attention`이 비어 있지 않으면 보인다. ADR 파일은 건드리지 않는다."""
    item["attention"] = {"kind": "write_conflict", "reason": reason, "since": _now(ctx), **detail}
    item["history"].append(_event(ctx, event="blocked", reason=reason, by="graph"))
    save_item(ctx, item)
    return {"action": "blocked", "reason": reason, "result": _result(ctx, item, blocked=item["attention"]), **detail}


def recover(ctx, item_id, *, after=None, final_lifecycle="DONE", exporter=None):
    """쓰다 만 것의 회복 — §5 표 네 줄. 돌려주는 action:
    none(의도 없음) · already_done(완료) · resumed_write(ADR 파일이 없어 이어 씀) · renumbered(번호를 남이 가져가서 다시 정함)
    · completed(파일이 있고 해시 같음 — 완료만 적음) · blocked(파일이 있는데 해시 다름 — 사람이 고친 것, 덮지 않는다)."""
    item = read_item(ctx, item_id)
    if item is None:
        return {"action": "none", "result": None}
    wi = item.get("write_intent")
    if not wi:
        return {"action": "none", "result": _result(ctx, item)}
    if wi["state"] == "done":
        return {"action": "already_done", "result": _result(ctx, item, recovery={"action": "already_done"})}
    if sha(wi.get("content") or "") != wi["content_hash"]:  # 의도 속 내용이 해시와 안 맞는다 — 손으로 고친 별도 파일
        return _block(ctx, item, "intent_corrupt", expected_hash=wi["content_hash"])

    path = _abs(ctx, wi["path"])
    if os.path.exists(path):
        found = _file_hash(path)
        if found == wi["content_hash"]:  # 3과 4 사이: 우리가 쓴 그대로 — 완료만 적는다
            errs = _validate(ctx, wi["adr"])
            exp = _complete(ctx, item, errs, final_lifecycle, exporter, event="adr_confirmed")
            return {"action": "completed", "result": _result(ctx, item, errs, recovery={"action": "completed"}, export=exp)}
        # 3과 4 사이, 해시 다름: 사람이 고친 것이다 → 덮지 않고 보인다 (결정 1)
        return _block(ctx, item, "hash_differs", path=wi["path"], expected_hash=wi["content_hash"], found_hash=found)

    action = "resumed_write"
    taken = [f for f in _id_files(ctx, wi["adr"]) if f != path]
    if taken:  # 2와 3 사이, 그 사이 다른 것이 번호를 가져갔다 → 번호를 다시 정하고 의도를 고친다
        nid = radar(ctx, "next-id", ctx.adr_dir)["next_id"]
        slug_part = os.path.basename(path)[5:]
        path = os.path.join(os.path.abspath(ctx.adr_dir), f"{nid}-{slug_part}")
        old = wi["adr"]
        wi.update(adr=nid, path=_rel(ctx, path))
        item["history"].append(_event(ctx, event="renumbered", **{"from": old, "to_adr": nid}, by="graph"))
        save_item(ctx, item)
        _hook(after, "intent")
        action = "renumbered"
    _create_adr_file(ctx, path, wi["content"])  # 2와 3 사이: 쓴다
    errs = _validate(ctx, wi["adr"])
    _hook(after, "adr")
    exp = _complete(ctx, item, errs, final_lifecycle, exporter)
    _hook(after, "done")
    return {"action": action, "result": _result(ctx, item, errs, recovery={"action": action}, export=exp)}


def rewrite_adr(ctx, item_id, adr_id, draft, *, after=None):
    """이미 쓴 우리 파일을 고쳐 다시 쓴다 (detect의 repair). 같은 write-ahead — 의도(새 해시) → 파일 → 완료.
    지금 파일이 우리가 마지막에 쓴 것과 다르면(사람이 고쳤다) 덮지 않는다. 돌려주는 값: 남은 validate 오류 (막히면 한 줄 오류)."""
    item = read_item(ctx, item_id)
    if not item or not item.get("write_intent"):
        raise HitlError(f"{item_id}: 다시 쓸 의도가 없다")
    wi = item["write_intent"]
    path = _abs(ctx, wi["path"])
    if wi["state"] != "done" or not os.path.exists(path) or _file_hash(path) != wi["content_hash"]:
        _block(ctx, item, "hash_differs", path=wi["path"], expected_hash=wi["content_hash"],
               found_hash=_file_hash(path) if os.path.exists(path) else None)
        return ["blocked: 파일이 우리가 쓴 것과 다르다 — 덮지 않았다"]
    content = ops.render(draft, ctx.now)
    wi.update(content_hash=sha(content), content=content, state="intended")
    item["history"].append(_event(ctx, event="rewrite_intended", adr=adr_id, draft_hash=wi["content_hash"], by="graph"))
    save_item(ctx, item)
    _hook(after, "intent")
    ctx.record("Write", {"file_path": path})
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    errs = _validate(ctx, adr_id)
    _hook(after, "adr")
    _complete(ctx, item, errs, None, None, event="adr_rewritten")
    return errs


# ── 재개 프로토콜 (결정 3, §4) ───────────────────────────────────────

def check_resume(ctx, item_id, *, stored_fp, scope, need_item=False, fp=None, after=None, final_lifecycle="DONE"):
    """재개 1~3단계. 돌려주는 action:
      orphan      별도 파일이 있어야 하는데 없다 → 멈추고 사람에게 보인다 (need_item=True일 때)
      recovered   쓰다 만 것을 회복했다 (recovery 안에 세부) — 끝난 항목이다
      done        이미 완료된 항목이다
      blocked     회복이 막혔다 (해시 다름) 또는 별도 파일 형식 오류 — 사람에게 보인다
      stale       지문이 다르다 → changes(무엇이 바뀌었나)와 함께 다시 interrupt. new_fp는 「계속」할 때 근거가 된다
      proceed     그대로 재개
    capture는 승인 전에 별도 파일이 없다(R9, §3-1) — need_item=False. 승인 뒤에 쓰다 죽었다면 파일이 있고 의도가 남아 있다."""
    try:
        item = read_item(ctx, item_id)
    except ItemInvalid as e:
        return {"action": "blocked", "reason": "item_invalid", "errors": e.errors}
    if item is None and need_item:
        return {"action": "orphan", "reason": "별도 파일이 없다 — 고아 checkpoint"}
    if item is not None and item.get("write_intent"):
        if item["write_intent"]["state"] == "done":
            return {"action": "done", "item": item}
        rec = recover(ctx, item_id, after=after, final_lifecycle=final_lifecycle)
        if rec["action"] == "blocked":
            return {"action": "blocked", "reason": rec["reason"], "recovery": rec}
        return {"action": "recovered", "recovery": rec}
    fp = fp or fingerprint_for(ctx, (stored_fp or {}).get("impl"))
    new = fp.compute(ctx, scope)
    if stored_fp is not None and fp.same(stored_fp, new):  # MUT:compare
        return {"action": "proceed", "new_fp": new}
    changes = fp.diff(stored_fp, new) if stored_fp is not None else ["멈출 때의 지문이 없다 — 무엇이 바뀌었는지 비교할 수 없다"]
    return {"action": "stale", "changes": changes, "new_fp": new}


STALE_ANSWERS = ("continue", "rejudge", "discard")  # 계속 · 다시 판정 · 버림


def close_item(ctx, item_id, reason):
    """항목을 끝낸다 — 별도 파일이 있으면 lifecycle을 닫고 history를 남긴다 (파일은 지우지 않는다). 없으면 아무것도 쓰지 않는다 (R9)."""
    item = read_item(ctx, item_id)
    if item is None:
        return False
    item["lifecycle"] = "CLOSED"
    item["history"].append(_event(ctx, to="CLOSED", reason=reason, by="human"))
    save_item(ctx, item)
    return True


# ── edit (결정 6, §5-1) ──────────────────────────────────────────────

def affected_errors(ctx, draft):
    """초안의 affected repo가 레이더 대상(radar-targets.json)인가 (R18, HYO-95) — 아니면 쓰지 않고 사람에게 묻는다.
    「unknown」처럼 모델이 지은 repo나 빈 affected가 조용히 ADR에 들어가지 않게 한다. 대상 목록을 모르면(파일 없음) repo는 판단하지 않는다."""
    d = draft.model_dump() if hasattr(draft, "model_dump") else (draft or {})
    if not d.get("affected"):  # 비어 있으면 대상 목록과 무관하게 막는다 — 2026-10-11 실측: 자동 모드에서 모델이 affected를 비웠다
        return ["affected가 비었다 — repo·component를 정해서 승인한다 (R18)"]
    if not ctx.targets:
        return []
    bad = sorted({(a or {}).get("repo") or "(빈 값)" for a in d.get("affected") or [] if (a or {}).get("repo") not in ctx.targets})
    return [f"affected repo {bad}는 레이더 대상이 아니다 — 대상: {sorted(ctx.targets)} (R18). repo를 고쳐서 승인한다"] if bad else []


def check_edit(ctx, edited):
    """고친 초안을 **쓰기 전에** 검사한다 → (AdrDraft | None, 오류 목록). 모델 repair가 아니라 사람에게 돌려준다.
    1) status·번호 칸이 없다  2) 초안 스키마  3) radar.py validate — 임시 폴더에 지금의 ADR들 + 이 초안을 번호를 붙여 놓고 돌린다 (ADR repo는 건드리지 않는다)."""
    if not isinstance(edited, dict):
        return None, ["고친 초안이 매핑이 아니다"]
    errs = [f"{k} 칸은 edit으로 바꿀 수 없다 — status는 ADR 파일에서 사람이 바꾸고(R8) 번호는 저장할 때 정해진다" for k in EDIT_FORBIDDEN if k in edited]
    errs += [f"모르는 칸: {k}" for k in edited if k not in AdrDraft.model_fields and k not in EDIT_FORBIDDEN]
    try:
        draft = AdrDraft.model_validate({k: v for k, v in edited.items() if k in AdrDraft.model_fields})
    except ValidationError as e:
        return None, errs + [f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in e.errors()]
    errs += affected_errors(ctx, draft)
    if errs:
        return None, errs
    q = _quiet(ctx)
    nid = radar(q, "next-id", q.adr_dir)["next_id"]
    tmp = tempfile.mkdtemp(prefix="radar-edit-", dir=ctx.env.get("TMP") or ctx.env.get("TEMP") or None)
    try:
        for p in _adr_files(ctx, world=False):
            shutil.copy(p, tmp)
        with open(os.path.join(tmp, f"{nid}-{ops.slug(draft.technology)}.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(ops.render(draft, ctx.now))
        v = radar(q, "validate", tmp, ok_codes=(0, 1))  # MUT:edit_validate
        verr = next((a["errors"] for a in v["adrs"] if a["id"] == nid), ["validate에 없음"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (None, [f"validate: {e}" for e in verr]) if verr else (draft, [])


# ── 내보내기 흐름 (결정 8, §5-2) ─────────────────────────────────────

def _hold(ctx, item, state, kind, reason, **detail):
    """내보내기가 멈췄다 — 별도 파일에 상태와 이유를 남겨 사람에게 보인다 (ADR 파일은 건드리지 않는다). 처음 멈춘 시각(since)은 유지한다."""
    prev = item.get("export") or {}
    since = prev.get("since") if prev.get("state") == state else None
    item["export"] = {"state": state, "since": since or _now(ctx), **detail}
    item["attention"] = {"kind": kind, "reason": reason, "since": item["export"]["since"], **detail}
    item["history"].append(_event(ctx, event=f"export_{state}", reason=reason, by="graph"))
    save_item(ctx, item)


def _own_exports(ctx, item, old, new):
    """같은 실행에서 **제품이 직접 내보낸** 새 ADR의 파일 이름 — 지문 비교에서 넘긴다 (설계 §5-2 「구현에서 정한 것」 ①).
    이름·시간 같은 추측이 아니라 별도 파일의 기록으로 가린다. 넘기려면 이름 하나마다 다음이 전부 맞아야 한다:
      1. 그 이름이 승인 근거 지문(old)에는 없고 지금(new)에만 있다 — 지문 차이의 「새 ADR」이다
      2. 다른 항목의 별도 파일이 그 ADR을 썼다 (write_intent.path) · 쓰기가 끝났고 내보내기가 끝났다 (export.state=done)
      3. 그 항목의 run_id가 이 항목의 run_id와 같다 (둘 다 있어야 한다 — 없으면 같은 실행으로 보지 않는다)
      4. 지금 파일의 해시가 내보낼 때 적어 둔 content_hash와 같다 — 사람이 그 뒤 고쳤으면 바깥 변화다"""
    rid = item.get("run_id")
    if not rid or old is None:
        return set()
    cand = set(new.get("files", {})) - set(old.get("files", {}))
    if not cand:
        return set()
    d, own = items_dir(ctx), set()
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not f.endswith(".yaml") or f[:-5] == item["id"]:
            continue
        try:
            o = yaml.safe_load(open(os.path.join(d, f), encoding="utf-8").read()) or {}
        except (yaml.YAMLError, OSError):
            continue
        wi = o.get("write_intent") if isinstance(o, dict) else None
        if not isinstance(wi, dict) or o.get("run_id") != rid:  # MUT:own_run
            continue
        name = os.path.basename(wi.get("path") or "")
        path = _abs(ctx, wi["path"]) if wi.get("path") else None
        if (name in cand and wi.get("state") == "done" and (o.get("export") or {}).get("state") == "done"
                and path and os.path.exists(path) and _file_hash(path) == wi.get("content_hash")):  # MUT:own_hash
            own.add(name)
    return own


def _without(new, old, names):
    """new에서 names의 ADR(과 그 번호가 match 결과에 새로 낀 것)을 뺀 사본 — 비교와 차이 요약에만 쓰고 저장하지 않는다."""
    out = copy.deepcopy(new)
    for n in names:
        out.get("files", {}).pop(n, None)
        out.get("detail", {}).pop(n, None)
        out.get("meta", {}).pop(n, None)
        for k, ids in (out.get("match") or {}).items():
            if n[:4] in ids and n[:4] not in (old.get("match") or {}).get(k, []):
                out["match"][k] = [i for i in ids if i != n[:4]]
    return out


def _stale_info(ctx, item):
    """재개 검사 — 승인 근거 지문과 지금 세상(내보내지 않은 로컬 초안 제외)을 비교한다. 같으면 None.
    같은 실행에서 제품이 내보낸 새 ADR은 비교에서 넘긴다 (`_own_exports`) — A′·B′ 둘 다. new_fp는 넘기기 전의 온전한 세상이다.
    바깥 변화는 없고 넘긴 것만 있었으면 {"state": "same", "own_exports": [...]}."""
    old = item.get("fingerprint")
    fp = fingerprint_for(ctx, (old or {}).get("impl"))
    new = fp.compute(_quiet(ctx), item.get("scope") or {})
    own = sorted(_own_exports(ctx, item, old, new))
    cmp_new = _without(new, old, own) if own else new
    if old is not None and fp.same(old, cmp_new):  # MUT:recheck_same
        return {"state": "same", "own_exports": own} if own else None
    changes = fp.diff(old, cmp_new) if old is not None else ["승인 근거 지문이 없다 — 무엇이 바뀌었는지 비교할 수 없다"]
    return {"state": "stale", "changes": changes, "new_fp": new, "own_exports": own}


def export_item(ctx, item_id, exporter=None, *, answer=None):
    """쓰기가 끝난 항목의 ADR 파일 + 별도 파일을 커밋 1개로 main에 내보낸다. 돌려주는 state:
      exported 커밋·push 끝 · stale 원격이 앞서 있었고(또는 대기 중에) 세상이 달라졌다 — 커밋하지 않았다, `changes`를 보이고 사람의 답을 기다린다
      blocked 막혔다(reason) · skipped 내보내기가 꺼져 있다(no-op) · already_exported
    answer="continue"는 stale을 알고 넘기겠다는 사람의 답이다 — 그때의 세상이 승인 근거가 된다 (그 사이 또 바뀌면 다시 묻는다)."""
    ex = exporter or exporter_for(ctx)
    if not ex.active:
        return {"state": "skipped", "reason": "no_exporter"}
    item = read_item(ctx, item_id)
    wi = (item or {}).get("write_intent")
    if not item or not wi or wi["state"] != "done":
        raise HitlError(f"{item_id}: 쓰기가 끝난 항목이 아니다 — 내보낼 것이 없다")
    if (item.get("export") or {}).get("state") == "done":
        return {"state": "already_exported", "item": item_id}
    adr_path = _abs(ctx, wi["path"])
    if not os.path.exists(adr_path) or _file_hash(adr_path) != wi["content_hash"]:  # 결정 1 — 사람이 고쳤거나 사라진 파일을 덮어 커밋하지 않는다
        found = _file_hash(adr_path) if os.path.exists(adr_path) else None
        _hold(ctx, item, "blocked", "export_blocked", "hash_differs" if found else "adr_missing", path=wi["path"], expected_hash=wi["content_hash"], found_hash=found)
        return {"state": "blocked", "reason": "hash_differs" if found else "adr_missing", "item": item_id}

    snap = copy.deepcopy(item)
    accepted = {"fp": None}
    skipped = set()  # 지문 비교에서 넘긴 같은 실행의 내보내기 — 커밋하는 별도 파일의 history에 남긴다
    paths = [wi["path"], _rel(ctx, item_path(ctx, item_id))]

    def check():
        taken = [os.path.basename(f) for f in _id_files(ctx, wi["adr"]) if f != adr_path]
        if taken:  # 그 사이 같은 번호의 다른 ADR이 들어왔다 — 중복 번호를 커밋하지 않는다
            return {"state": "blocked", "reason": "number_taken", "files": taken}
        info = _stale_info(ctx, item)
        if info is None:
            return None
        if info["state"] == "same":  # 바깥 변화는 없고 같은 실행의 내보내기만 있다 — 기록해 두고 넘긴다
            skipped.update(info["own_exports"])
            return None
        if answer == "continue" and accepted["fp"] in (None, info["new_fp"]):
            accepted["fp"] = info["new_fp"]
            return None
        return info

    def before():
        if accepted["fp"] is not None:
            item["fingerprint"] = accepted["fp"]
            item["history"].append(_event(ctx, event="stale_accepted", by="human"))
        if skipped:
            item["history"].append(_event(ctx, event="own_exports_skipped", adrs=sorted(skipped), run_id=item.get("run_id"), by="graph"))
        item["lifecycle"] = "DONE"
        item["export"] = {"state": "done"}
        item.pop("attention", None)
        item["history"].append(_event(ctx, event="exported", adr=wi["adr"], by="graph"))
        save_item(ctx, item)

    def undo():  # push가 경합으로 거부돼 커밋을 취소했다 — 별도 파일도 커밋 전 상태로
        item.clear()
        item.update(copy.deepcopy(snap))
        save_item(ctx, item)

    msg = f"{item_id}: ADR {wi['adr']} 추가\n\nitem: {item_id}\nkind: {item.get('kind')}\nadr: {wi['adr']}\n"
    res = ex.publish(paths, msg, check=check, before=before, undo=undo)
    res = {k: v for k, v in res.items() if k != "new_fp"}
    if res["state"] == "stale":
        _hold(ctx, item, "stale", "export_stale", "원격에 사람의 변경이 들어와 세상이 달라졌다 — 계속·다시 판정·버림", changes=res["changes"])
    elif res["state"] == "blocked":
        _hold(ctx, item, "blocked", "export_blocked", res.get("reason", "blocked"),
              **{k: v for k, v in res.items() if k not in ("state", "reason")})
    return {**res, "item": item_id, "adr": wi["adr"]}


def review_item(ctx, item_id, decision, *, stale_answer=None, exporter=None):
    """검토 대기·내보내기 보류 항목에 사람의 답을 넣는다 (웹 Inbox HYO-51 전의 입구 — review.py가 부른다).
      approve  내보낸다. 재개 검사에서 달라졌으면 changes를 돌려주고 멈춘다 — stale_answer: continue | rejudge | discard
      reject   ADR 초안(아직 커밋 안 된 새 파일)을 지우고 항목을 닫는다 (닫힌 기록은 별도 파일로 커밋한다)"""
    ex = exporter or exporter_for(ctx)
    if not ex.active:
        raise HitlError("내보내기가 꺼져 있다 (RADAR_EXPORT=git · dry-run 아님이 필요하다) — 검토 답을 반영하지 않았다")
    if decision not in ("approve", "reject"):
        raise HitlError(f"모르는 답 {decision!r} — approve | reject")
    if stale_answer not in (None, *STALE_ANSWERS):
        raise HitlError(f"모르는 stale 답 {stale_answer!r}")
    item = read_item(ctx, item_id)
    if item is None:
        raise HitlError(f"{item_id}: 별도 파일이 없다")
    st = (item.get("export") or {}).get("state")
    if st not in PENDING_EXPORT:
        raise HitlError(f"{item_id}: 검토 대기 항목이 아니다 (export.state={st})")
    if decision == "reject":
        return _discard_draft(ctx, item, ex, "reject")
    if stale_answer in ("rejudge", "discard"):
        return _discard_draft(ctx, item, ex, stale_answer)
    return export_item(ctx, item_id, ex, answer=stale_answer)


def _discard_draft(ctx, item, ex, reason):
    """초안을 버리고 항목을 닫는다. 이미 커밋된 파일은 지우지 않고(= 기존 ADR을 건드리지 않는다), 사람이 고친 파일도 덮어 지우지 않는다."""
    wi = item.get("write_intent") or {}
    rel = wi.get("path")
    path = _abs(ctx, rel) if rel else None
    if path and os.path.exists(path):
        if ex.is_tracked(rel):
            raise HitlError(f"{item['id']}: {rel}은 이미 커밋된 파일이다 — 지우지 않았다 (되돌리기는 git revert)")
        if _file_hash(path) != wi.get("content_hash"):
            _hold(ctx, item, "blocked", "export_blocked", "hash_differs", path=rel, expected_hash=wi.get("content_hash"), found_hash=_file_hash(path))
            return {"state": "blocked", "reason": "hash_differs", "item": item["id"]}
        os.remove(path)
    item["lifecycle"] = "CLOSED"
    item["export"] = {"state": "closed", "reason": reason}
    item.pop("attention", None)
    item["history"].append(_event(ctx, to="CLOSED", reason=reason, by="human"))
    save_item(ctx, item)
    res = ex.publish([_rel(ctx, item_path(ctx, item["id"]))], f"{item['id']}: 검토 {reason} — ADR {wi.get('adr')} 초안 폐기\n\nitem: {item['id']}\n")
    return {**res, "outcome": reason, "item": item["id"], "adr": wi.get("adr")}


def sync_exporter(ctx, exporter=None):
    """재개 전에 원격을 따라잡는다 (fetch → fast-forward). 막히면 그 이유를 돌려주고, 꺼져 있거나 문제없으면 None."""
    ex = exporter or exporter_for(ctx)
    if not ex.active:
        return None
    s = ex.sync()
    return s if s["state"] == "blocked" else None


def record_only_errors(old_text, new_text):
    """기존 ADR 두 판이 기록용 칸(MACHINE_FIELDS · MACHINE_KEYS_ANYWHERE)만 다른가 — 기록 커밋의 내용 검사. 오류 문자열 | None.
    지문과 같은 기준(`_strip_machine`)이다: radar.py가 yaml을 다시 쓰므로 글자가 아니라 파싱한 값으로 비교한다. 본문은 글자 그대로."""
    def parts(text):
        text = text.replace("\r\n", "\n")
        m = _FM.match(text)
        try:
            fm = yaml.safe_load(m.group(1)) if m else None
        except yaml.YAMLError:
            fm = None
        return (_strip_machine(fm), text[m.end():]) if isinstance(fm, dict) else None

    old, new = parts(old_text), parts(new_text)
    if old is None or new is None:
        return "프론트매터를 못 읽었다"
    if old[1] != new[1]:
        return "본문이 바뀌었다"
    keys = sorted(k for k in set(old[0]) | set(new[0]) if _canon(old[0].get(k)) != _canon(new[0].get(k)))
    return f"칸이 바뀌었다: {keys}" if keys else None


def export_record(ctx, paths, message, exporter=None):
    """revisit 직후 — radar.py decide --apply가 쓴 기록용 칸을 커밋 1개로 main에 (사용자 10/26). 내용 검사는 `record_only_errors`.
    paths는 절대 경로(decide의 files_written). git 내보내기가 아니면(eval·dry-run·스텁·로컬 사본) skipped."""
    ex = exporter or exporter_for(ctx)
    if not hasattr(ex, "publish_record"):
        return {"state": "skipped", "reason": "no_exporter"}
    return ex.publish_record([_rel(ctx, p) for p in paths], message, only_record=lambda _rel_, old, new: record_only_errors(old, new))


def rejected_sources(ctx):
    """사람이 거부한 detect 초안의 소식 → {피드 항목 id: 항목 id} (HYO-142). 거부만 센다 — 「버림」(stale 답)·「다시 판정」은
    소식에 대한 판단이 아니다. 기술이 아니라 소식 하나 단위다: 같은 기술의 새 릴리스(다른 id)는 그대로 올라온다."""
    d, out = items_dir(ctx), {}
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".yaml"):
            continue
        try:
            it = read_item(ctx, f[:-5]) or {}
        except (ItemInvalid, HitlError):
            continue
        ex = it.get("export") or {}
        if it.get("kind") == "detect" and ex.get("state") == "closed" and ex.get("reason") == "reject":
            for s in it.get("sources") or []:
                out.setdefault(s, it["id"])
    return out


def review_queue(ctx):
    """검토 대기·보류 항목 — Inbox가 보일 목록 (대기 일수는 since에서)."""
    d, out = items_dir(ctx), []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".yaml"):
            continue
        try:
            it = read_item(ctx, f[:-5])
        except (ItemInvalid, HitlError):
            continue
        ex = (it or {}).get("export") or {}
        if ex.get("state") in PENDING_EXPORT:
            out.append({"item": it["id"], "kind": it["kind"], "adr": (it.get("write_intent") or {}).get("adr"), "export": ex, "created": it["created"]})
    return out

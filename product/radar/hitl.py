"""HITL 상태 — 항목 별도 파일 · write-ahead 쓰기 · 재개 검사 · edit 검사 (설계 docs/design/hitl-state.md, HYO-44).

원칙은 하나다: **ADR 파일이 이긴다** (결정 1). 제품 쪽 상태(checkpoint · 별도 파일)가 ADR 파일과 어긋나면 파일을 믿고,
제품 쪽을 고치거나 사람에게 보인다 — 사람이 고쳤을지 모르는 파일을 덮지 않는다.

  결정 2  `<ADR repo 루트>/.radar/items/<item-id>.yaml` — 항목 하나당 파일 하나. `status` 칸이 없다 (R8)
  결정 3  재개: 별도 파일 읽기 → 쓰다 만 것 회복 → 지문 비교 → 다르면 차이를 보여 다시 interrupt (계속 · 다시 판정 · 버림)
  결정 4  쓰기: 번호 → 별도 파일에 의도 → ADR 파일 + validate → 완료 (write-ahead). 각 단계 뒤 파일을 닫는다
  결정 5  thread_id = item-id (capture.py)
  결정 6  edit: 고친 초안도 validate를 지난다 — 실패하면 쓰지 않고 오류를 돌려준다. status·번호는 못 바꾼다

**Q2(지문에 무엇을 넣나)는 미정이다 — 사용자가 다음 대화에서 정한다.** `Fingerprint` 인터페이스 뒤에 두 구현을 두고
기본값은 (A) `RepoHashFingerprint`(docs/adr 파일 해시 전체)다. (B) `RelatedMatchFingerprint`는 정해지면 바꿔 끼운다.
**Q3(git 내보내기)도 미정이다** — 로컬 파일 쓰기까지만 하고 `Exporter` 자리(no-op)만 둔다.
"""

import abc
import dataclasses
import datetime as dt
import glob
import hashlib
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


# ── 지문 (결정 3) — Q2 미정: 사용자가 다음 대화에서 정한다 ─────────────────

_FM = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _adr_files(ctx):
    return sorted(glob.glob(os.path.join(os.path.abspath(ctx.adr_dir), "*.md")))


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
    """(A) ADR repo 전체 해시 — docs/adr/*.md 파일 해시 전부. git이 없어도 된다.
    무엇이든 바뀌면 다르다고 한다: 빠뜨리는 변화는 없지만 무관한 변화에도 다시 묻는다. 기본값 (Q2 미정)."""
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
    """(B) 관련 ADR 파일 해시 + 같은 기술 `radar.py match` 결과.
    관련 = 초안이 적은 관련 ADR + match가 돌려준 exact·overlap·related. 필요한 것만 다시 묻지만 「관련」의 정의에 기댄다."""
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
        return {"exact": sorted(exact), "overlap": sorted(overlap), "related": sorted(related)}

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


FINGERPRINTS = {f.name: f for f in (RepoHashFingerprint(), RelatedMatchFingerprint())}
DEFAULT_FINGERPRINT = "repo_hash"  # Q2 미정 — 사용자가 다음 대화에서 정한다. 바꾸려면 이 한 줄 (또는 환경변수 RADAR_FINGERPRINT)


def fingerprint_for(ctx=None, name=None):
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


# ── git 내보내기 자리 (Q3 미정) ─────────────────────────────────────

class Exporter:
    """ADR 파일·별도 파일을 git으로 내보내는 자리. 기본은 아무것도 안 한다 — 승인 = 바로 커밋인지, 브랜치 + PR인지(Q3)는 사용자가 정한다."""

    def export(self, ctx, paths, message):
        return None


DEFAULT_EXPORTER = Exporter()


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


def _result(ctx, item, errs=None, recovery=None, blocked=None):
    wi = item.get("write_intent") or {}
    out = {"written": _abs(ctx, wi["path"]) if wi and not blocked else None, "id": wi.get("adr"), "validate_errors": errs or [],
           "item": item["id"], "state": wi.get("state")}
    if recovery:
        out["recovery"] = recovery
    if blocked:
        out["blocked"] = blocked
    return out


def _complete(ctx, item, errs, final_lifecycle, exporter, event="adr_written"):
    wi = item["write_intent"]
    wi["state"] = "done"
    wi.pop("content", None)  # 완료되면 본문은 ADR 파일에 있다 — 해시만 남긴다
    if errs:
        wi["validate_errors"] = list(errs)
    else:
        wi.pop("validate_errors", None)
    item["adr"] = wi["adr"]
    ev = {"event": event, "adr": wi["adr"], "by": "graph"}
    if errs:
        ev["validate_errors"] = len(errs)
    if final_lifecycle:
        item["lifecycle"] = final_lifecycle
        ev["to"] = final_lifecycle
    item["history"].append(_event(ctx, **ev))
    save_item(ctx, item)  # 4. 완료
    (exporter or DEFAULT_EXPORTER).export(ctx, [_abs(ctx, wi["path"]), item_path(ctx, item["id"])], f"{item['id']}: ADR {wi['adr']}")


def write_adr(ctx, item_id, kind, draft, *, lifecycle="USER_REVIEW", final_lifecycle="DONE", by="human",
              approval=None, after=None, exporter=None):
    """승인된 초안을 쓴다 — 1 번호 → 2 별도 파일에 의도 → 3 ADR 파일 + validate → 4 완료. 각 단계 뒤 파일은 닫혀 있다.

    approval: {"decision": "approve"|"edit", "fingerprint": 승인 근거 지문, "draft_hash_original": edit 전 초안의 해시}.
    이미 의도가 있으면 새로 쓰지 않고 회복한다 (그래프가 같은 도구 노드를 다시 돌려도 ADR이 두 개가 되지 않는다 — S3).
    """
    approval = approval or {}
    item = read_item(ctx, item_id)
    if item and item.get("write_intent"):
        if item["write_intent"]["state"] == "done":
            return _result(ctx, item, recovery={"action": "already_done"})
        rec = recover(ctx, item_id, after=after, final_lifecycle=final_lifecycle, exporter=exporter)
        return rec["result"]

    nid = radar(ctx, "next-id", ctx.adr_dir)["next_id"]  # 1. 번호는 쓰는 이 순간에 정한다
    _hook(after, "number")
    content = ops.render(draft, ctx.now)
    chash = sha(content)
    path = os.path.join(os.path.abspath(ctx.adr_dir), f"{nid}-{ops.slug(draft.technology)}.md")

    item = item or new_item(ctx, item_id, kind, lifecycle)
    item["lifecycle"] = lifecycle
    item["fingerprint"] = approval.get("fingerprint")
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

    _complete(ctx, item, errs, final_lifecycle, exporter)
    _hook(after, "done")
    return _result(ctx, item, errs)


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
            _complete(ctx, item, errs, final_lifecycle, exporter, event="adr_confirmed")
            return {"action": "completed", "result": _result(ctx, item, errs, recovery={"action": "completed"})}
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
    _complete(ctx, item, errs, final_lifecycle, exporter)
    _hook(after, "done")
    return {"action": action, "result": _result(ctx, item, errs, recovery={"action": action})}


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
    if errs:
        return None, errs
    q = _quiet(ctx)
    nid = radar(q, "next-id", q.adr_dir)["next_id"]
    tmp = tempfile.mkdtemp(prefix="radar-edit-", dir=ctx.env.get("TMP") or ctx.env.get("TEMP") or None)
    try:
        for p in _adr_files(ctx):
            shutil.copy(p, tmp)
        with open(os.path.join(tmp, f"{nid}-{ops.slug(draft.technology)}.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(ops.render(draft, ctx.now))
        v = radar(q, "validate", tmp, ok_codes=(0, 1))  # MUT:edit_validate
        verr = next((a["errors"] for a in v["adrs"] if a["id"] == nid), ["validate에 없음"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (None, [f"validate: {e}" for e in verr]) if verr else (draft, [])

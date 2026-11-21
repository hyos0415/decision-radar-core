"""HITL 상태 회귀 — 실제 API 없이 스텁으로 돈다 (설계 docs/design/hitl-state.md, HYO-44).

  .venv/Scripts/python product/radar/test_hitl.py

1. 회복      write-ahead 각 단계 사이에서 죽이고 재개한다 — §5 표 네 줄이 각각 맞게 회복되나. 「해시 다름」은 덮지 않나
             (함수 단위 + 실제 프로세스를 os._exit로 죽이는 것 하나)
2. 낡음      승인 대기 중 ADR repo를 바꾼 뒤 재개 — S1(같은 기술 ADR이 새로 생김)·S4(관련 ADR status 변경)가 지문 (A)·(B) 둘 다에서 다시 interrupt되나
3. S2        두 항목이 동시에 대기해도 서로 안 덮나
4. edit      고친 초안 저장 · validate 실패한 edit은 안 쓰임 · status·번호 못 바꿈
5. 변이      일부러 틀리게 고친 hitl.py(해시 다름인데 덮어씀, 지문 비교 생략, 의도 없이 바로 씀 …)를 위 검사가 잡나 (규율 3 — 통과는 증거가 아니다)

그래프 검사는 스텁 모델로 capture 2턴을 실제로 돌린다. 지문 구현은 Q2 미정이라 (A)·(B) 둘 다 본다.
"""

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # product/

import yaml  # noqa: E402

from radar import capture, hitl  # noqa: E402
from radar.stub_model import StubChat  # noqa: E402
from common import ops  # noqa: E402
from common.core import SKILL_RADAR, Ctx  # noqa: E402
from common.eval_adapter import capture_script  # noqa: E402
from common.schemas import AdrDraft  # noqa: E402
from common.eval_adapter import rc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = rc.NOW
FIXTURE = os.path.join(rc.FIX, "adr-fixture")
fails = []
FORGED = "adopted"  # edit·별도 파일로 몰래 넣어 보는 status 값 (R8 — 어디서도 받아들이면 안 된다)


def check(name, ok, detail=None):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or detail is None else f" — {detail}"))
    if not ok:
        fails.append(name)


# ── 세계 ─────────────────────────────────────────────────────────────

class World:
    """실행마다 새 ADR repo(픽스처 사본) + checkpoint 파일."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="jhs-hitl-")
        self.root = os.path.join(self.dir, "adr-fixture")
        shutil.copytree(FIXTURE, self.root)
        os.makedirs(os.path.join(self.dir, "tmp"))
        self.adr = os.path.join(self.root, "docs", "adr")
        self.state = os.path.join(self.dir, "tmp", "capture-checkpoint.sqlite")

    def env(self):
        return {**os.environ, "RADAR_ADR_DIR": self.adr, "RADAR_TARGETS": os.path.join(self.root, "radar-targets.json"),
                "RADAR_DRY_RUN": "1", "TMP": os.path.join(self.dir, "tmp"), "TEMP": os.path.join(self.dir, "tmp"), "PYTHONIOENCODING": "utf-8"}

    def ctx(self, now=NOW):
        c = Ctx.from_env(self.env(), radar_py=SKILL_RADAR)
        c.now = now
        return c

    def adrs(self):
        return sorted(os.path.basename(p) for p in os.listdir(self.adr) if p.endswith(".md"))

    def adr_text(self, name):
        return open(os.path.join(self.adr, name), encoding="utf-8", newline="").read()

    def items(self):
        d = os.path.join(self.root, ".radar", "items")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def item(self, item_id):
        return yaml.safe_load(open(os.path.join(self.root, ".radar", "items", item_id + ".yaml"), encoding="utf-8"))

    def snapshot(self):
        """ADR repo 전체의 (이름, 해시) — 쓰기가 0건인지 비교한다."""
        out = {}
        for base, _, files in os.walk(self.root):
            for f in files:
                p = os.path.join(base, f)
                out[os.path.relpath(p, self.root)] = hitl._file_hash(p)
        return out

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


KOPII = capture_script("JHS-014")  # match·draft — 새 기술 KoPII-small (medical-mate/pii-guard), 관련 0003
D1 = AdrDraft.model_validate(KOPII["draft"])


def draft2():
    d = json.loads(json.dumps(KOPII["draft"]))
    d.update(title="FooNER로 verifier-core를 보완할지", technology="FooNER", use="검증 보완",
             affected=[{"repo": "finance-verifier", "component": "verifier-core"}], related=[])
    return d


def script_for(draft):
    return {**KOPII, "draft": draft, "match": {"technology": draft["technology"], "repo": draft["affected"][0]["repo"],
                                               "component": draft["affected"][0]["component"], "use": draft["use"]}}


def sub_file(path, old, new):
    """파일 안의 문자열을 바꾼다 (읽은 뒤 쓴다 — open(w)가 먼저 비우지 않게). 못 찾으면 테스트가 헛도는 것이니 멈춘다."""
    text = open(path, encoding="utf-8", newline="").read()
    assert old in text, (path, old)
    open(path, "w", encoding="utf-8", newline="").write(text.replace(old, new, 1))


class Crash(Exception):
    pass


def crash_at(step):
    def hook(name):
        if name == step:
            raise Crash(step)
    return hook


# ── 1. 회복 · 항목 파일 · 편집 검사 — 함수 단위. h를 바꿔 끼워 변이도 같은 검사를 돈다 ──────────

UNIT = []


def unit(f):
    UNIT.append(f)
    return f


def _crash_write(h, w, step, draft=D1, item="it-1"):
    ctx = w.ctx()
    try:
        h.write_adr(ctx, item, "capture", draft, after=crash_at(step))
    except Crash:
        return True
    return False


@unit
def u_normal(h, w):
    r = h.write_adr(w.ctx(), "it-1", "capture", D1)
    it = w.item("it-1")
    wi = it["write_intent"]
    return (r["id"] == "0006" and not r["validate_errors"] and "0006-kopii-small.md" in w.adrs() and wi["state"] == "done"
            and wi["adr"] == "0006" and it["adr"] == "0006" and "status" not in it and it["history"][0]["decision"] == "approve"
            and wi["content_hash"] == h.sha(w.adr_text("0006-kopii-small.md")), (r, it))


@unit
def u_crash_before_intent(h, w):
    """2 전에 죽음 — 의도 없음: 아무 일도 없었다 (별도 파일도 ADR도 없다). 다시 쓰면 정상으로 쓴다"""
    crashed = _crash_write(h, w, "number")
    before = (w.items(), w.adrs())
    rec = h.recover(w.ctx(), "it-1")
    ok1 = crashed and before == ([], [a for a in w.adrs()]) and rec["action"] == "none" and "0006-kopii-small.md" not in w.adrs()
    r = h.write_adr(w.ctx(), "it-1", "capture", D1)
    return ok1 and r["id"] == "0006" and w.adrs().count("0006-kopii-small.md") == 1, (before, rec, r)


@unit
def u_crash_after_intent(h, w):
    """2와 3 사이 — 의도 있음, 파일 없음: 쓴다"""
    crashed = _crash_write(h, w, "intent")
    it = w.item("it-1")
    pre = it["write_intent"]["state"] == "intended" and "0006-kopii-small.md" not in w.adrs()
    rec = h.recover(w.ctx(), "it-1")
    it2 = w.item("it-1")
    return (crashed and pre and rec["action"] == "resumed_write" and "0006-kopii-small.md" in w.adrs() and it2["write_intent"]["state"] == "done"
            and it2["write_intent"]["content_hash"] == h.sha(w.adr_text("0006-kopii-small.md"))), (rec, it2)


@unit
def u_crash_after_intent_number_taken(h, w):
    """2와 3 사이, 그 사이 다른 것이 0006을 가져갔다: 번호를 다시 정하고 의도를 고친다 — 남의 파일은 그대로"""
    crashed = _crash_write(h, w, "intent")
    other = "---\nstatus: adopted\ntechnology: Other\nuse: x\naffected: []\nevidence: []\n---\n# 사람이 만든 것\n"
    open(os.path.join(w.adr, "0006-other.md"), "w", encoding="utf-8", newline="\n").write(other)
    rec = h.recover(w.ctx(), "it-1")
    it = w.item("it-1")
    return (crashed and rec["action"] == "renumbered" and it["write_intent"]["adr"] == "0007" and "0007-kopii-small.md" in w.adrs()
            and w.adr_text("0006-other.md") == other and it["write_intent"]["state"] == "done"
            and any(e.get("event") == "renumbered" for e in it["history"])), (rec, w.adrs())


@unit
def u_crash_after_adr_same_hash(h, w):
    """3과 4 사이, 해시 같음: 완료만 적는다 — ADR 파일을 다시 쓰지 않는다"""
    crashed = _crash_write(h, w, "adr")
    path = os.path.join(w.adr, "0006-kopii-small.md")
    before = (hitl._file_hash(path), os.stat(path).st_mtime_ns)
    pre = w.item("it-1")["write_intent"]["state"] == "intended"
    c = w.ctx()
    rec = h.recover(c, "it-1")
    wrote = [e for e in c.events if e["message"]["content"][0]["name"] == "Write"]
    after = (hitl._file_hash(path), os.stat(path).st_mtime_ns)
    return (crashed and pre and rec["action"] == "completed" and not wrote and before == after
            and w.item("it-1")["write_intent"]["state"] == "done"), (rec, wrote)


@unit
def u_crash_after_adr_hash_differs(h, w):
    """3과 4 사이, 해시 다름: 사람이 고친 것 — 덮지 않고 사람에게 보이는 상태로 멈춘다 (결정 1)"""
    crashed = _crash_write(h, w, "adr")
    path = os.path.join(w.adr, "0006-kopii-small.md")
    human = open(path, encoding="utf-8").read().replace("제안 — regex를", "사람이 직접 고친 문장 — regex를")
    open(path, "w", encoding="utf-8", newline="\n").write(human)
    rec = h.recover(w.ctx(), "it-1")
    again = h.recover(w.ctx(), "it-1")
    it = w.item("it-1")
    return (crashed and rec["action"] == "blocked" and again["action"] == "blocked" and w.adr_text("0006-kopii-small.md") == human
            and it["write_intent"]["state"] == "intended" and it["attention"]["reason"] == "hash_differs"
            and it["attention"]["found_hash"] == h.sha(human) and w.adrs().count("0006-kopii-small.md") == 1), (rec, it.get("attention"))


@unit
def u_done_is_noop(h, w):
    """4 뒤 — 완료: 넘어간다. 별도 파일 바이트도 그대로"""
    h.write_adr(w.ctx(), "it-1", "capture", D1)
    p = os.path.join(w.root, ".radar", "items", "it-1.yaml")
    before = open(p, "rb").read()
    rec = h.recover(w.ctx(), "it-1")
    r2 = h.write_adr(w.ctx(), "it-1", "capture", D1)  # 같은 항목을 또 쓰라고 해도
    return rec["action"] == "already_done" and open(p, "rb").read() == before and r2["id"] == "0006" and len(w.adrs()) == 6, (rec, w.adrs())


@unit
def u_retry_after_adr_no_duplicate(h, w):
    """S3 — ADR은 썼는데 완료 전에 죽었다. 그래프가 같은 쓰기를 다시 부르면 같은 ADR이 두 개가 되지 않는다"""
    crashed = _crash_write(h, w, "adr")
    r = h.write_adr(w.ctx(), "it-1", "capture", D1)
    kopii = [a for a in w.adrs() if "kopii" in a]
    return crashed and kopii == ["0006-kopii-small.md"] and r["id"] == "0006" and w.item("it-1")["write_intent"]["state"] == "done", (kopii, r)


@unit
def u_crash_during_recovery(h, w):
    """회복 중에 또 죽어도 다시 회복하면 된다"""
    _crash_write(h, w, "intent")
    try:
        h.recover(w.ctx(), "it-1", after=crash_at("adr"))
        died = False
    except Crash:
        died = True
    rec = h.recover(w.ctx(), "it-1")
    return died and rec["action"] == "completed" and w.adrs().count("0006-kopii-small.md") == 1, rec


@unit
def u_check_resume_stale(h, w):
    """S1·S4 — 지문 (A)·(B) 모두 다시 interrupt (stale) / 무관한 변화는 (A)만 다시 묻는다"""
    ok, detail = True, {}
    for impl in ("repo_hash", "related_match"):
        fp = h.FINGERPRINTS[impl]
        scope = h.scope_of(D1)
        stored = fp.compute(w.ctx(), scope)
        same = h.check_resume(w.ctx(), "it-x", stored_fp=stored, scope=scope)
        # S4 — 관련 ADR 0003의 status가 바뀜
        p3 = os.path.join(w.adr, "0003-guardrails-pii.md")
        orig = open(p3, encoding="utf-8", newline="").read()
        open(p3, "w", encoding="utf-8", newline="").write(orig.replace("status: adopted", "status: rejected", 1))
        s4 = h.check_resume(w.ctx(), "it-x", stored_fp=stored, scope=scope)
        open(p3, "w", encoding="utf-8", newline="").write(orig)
        # S1 — 같은 기술·component의 ADR이 새로 생김
        new = os.path.join(w.adr, "0006-kopii-small.md")
        open(new, "w", encoding="utf-8", newline="\n").write(ops.render(D1, NOW))
        s1 = h.check_resume(w.ctx(), "it-x", stored_fp=stored, scope=scope)
        os.remove(new)
        # 무관한 변화 — 0005(retrieval · FAISS)
        p5 = os.path.join(w.adr, "0005-faiss-ivf.md")
        o5 = open(p5, encoding="utf-8", newline="").read()
        open(p5, "w", encoding="utf-8", newline="").write(o5 + "\n사람이 덧붙임\n")
        un = h.check_resume(w.ctx(), "it-x", stored_fp=stored, scope=scope)
        open(p5, "w", encoding="utf-8", newline="").write(o5)
        back = h.check_resume(w.ctx(), "it-x", stored_fp=stored, scope=scope)
        detail[impl] = {"same": same["action"], "s4": (s4["action"], s4.get("changes")), "s1": (s1["action"], s1.get("changes")), "unrelated": un["action"]}
        ok &= (same["action"] == "proceed" and back["action"] == "proceed" and s4["action"] == "stale" and s1["action"] == "stale"
               and any("0003" in c and "adopted" in c and "rejected" in c for c in s4["changes"])
               and any("0006" in c for c in s1["changes"])
               and un["action"] == ("stale" if impl == "repo_hash" else "proceed"))
    return ok, detail


@unit
def u_no_fingerprint_is_stale(h, w):
    """멈출 때의 지문이 없으면 같다고 하지 않는다 — 비교할 수 없다고 보인다"""
    r = h.check_resume(w.ctx(), "it-x", stored_fp=None, scope=h.scope_of(D1))
    return r["action"] == "stale" and r["changes"], r


@unit
def u_check_resume_orphan(h, w):
    r = h.check_resume(w.ctx(), "it-x", stored_fp=None, scope=h.scope_of(D1), need_item=True)
    return r["action"] == "orphan", r


@unit
def u_edit_checks(h, w):
    d = json.loads(json.dumps(KOPII["draft"]))
    edited = {**d, "decision": "사람이 고친 결정문"}
    before = w.snapshot()
    ok_draft, ok_errs = h.check_edit(w.ctx(), edited)
    st_draft, st_errs = h.check_edit(w.ctx(), {**d, "status": FORGED})
    id_draft, id_errs = h.check_edit(w.ctx(), {**d, "id": "0099"})
    bad_draft, bad_errs = h.check_edit(w.ctx(), {**d, "research_note": ["blog.devnews.example/kopii-review — 후기. 근거가 아니다"]})
    shape_draft, shape_errs = h.check_edit(w.ctx(), {k: v for k, v in d.items() if k != "title"})
    return (ok_draft is not None and not ok_errs and ok_draft.decision == "사람이 고친 결정문"
            and st_draft is None and any("status" in e for e in st_errs)
            and id_draft is None and any("id" in e for e in id_errs)
            and bad_draft is None and any("R17" in e for e in bad_errs)
            and shape_draft is None and shape_errs
            and w.snapshot() == before), {"status": st_errs, "id": id_errs, "validate": bad_errs, "shape": shape_errs}


@unit
def u_edit_hashes(h, w):
    """edit — 고친 초안의 해시로 의도·history (원래 해시도 함께)"""
    d = json.loads(json.dumps(KOPII["draft"]))
    edited, errs = h.check_edit(w.ctx(), {**d, "decision": "사람이 고친 결정문"})
    orig_h = h.sha(ops.render(D1, NOW))
    h.write_adr(w.ctx(), "it-1", "capture", edited, approval={"decision": "edit", "draft_hash_original": orig_h})
    it = w.item("it-1")
    ev = it["history"][0]
    chash = h.sha(w.adr_text("0006-kopii-small.md"))
    return (not errs and "사람이 고친 결정문" in w.adr_text("0006-kopii-small.md") and "\nstatus: proposed\n" in w.adr_text("0006-kopii-small.md")
            and it["write_intent"]["content_hash"] == chash and ev["draft_hash_edited"] == chash and ev["draft_hash_original"] == orig_h
            and orig_h != chash and ev["decision"] == "edit"), it["history"]


@unit
def u_item_file_rules(h, w):
    """별도 파일에 status 칸이 없다 (R8) — 쓰지도 읽지도 않는다. 승인 전에는 파일이 없다 (R9)"""
    pre = w.items()
    it = h.new_item(w.ctx(), "it-s", "capture", "USER_REVIEW")
    it["status"] = FORGED
    try:
        h.save_item(w.ctx(), it)
        wrote = True
    except h.ItemInvalid:
        wrote = False
    os.makedirs(os.path.join(w.root, ".radar", "items"), exist_ok=True)
    d = h.new_item(w.ctx(), "it-h", "capture", "USER_REVIEW")
    d["status"] = FORGED
    yaml.safe_dump(d, open(os.path.join(w.root, ".radar", "items", "it-h.yaml"), "w", encoding="utf-8"), allow_unicode=True)
    try:
        h.read_item(w.ctx(), "it-h")
        read = True
    except h.ItemInvalid:
        read = False
    return pre == [] and not wrote and not read and "it-s.yaml" not in w.items(), (wrote, read)


def run_unit(h):
    """UNIT 전부를 h(hitl 모듈이거나 변이본)로 돈다 → [(이름, 통과, 상세)]"""
    out = []
    for f in UNIT:
        w = World()
        try:
            ok, detail = f(h, w)
        except Exception as e:  # 변이본이 예외로 깨져도 「잡힌 것」이다
            ok, detail = False, f"{type(e).__name__}: {e}"
        finally:
            w.cleanup()
        out.append((f.__name__, bool(ok), detail))
    return out


# ── 변이 — 일부러 틀리게 고친 hitl.py ───────────────────────────────

SRC = open(os.path.join(HERE, "hitl.py"), encoding="utf-8").read()

OVERWRITE_OLD = '''        return _block(ctx, item, "hash_differs", path=wi["path"], expected_hash=wi["content_hash"], found_hash=found)
'''
OVERWRITE_NEW = '''        with open(path, "w", encoding="utf-8", newline="\\n") as f:
            f.write(wi["content"])
        errs = _validate(ctx, wi["adr"])
        _complete(ctx, item, errs, final_lifecycle, exporter)
        return {"action": "completed", "result": _result(ctx, item, errs)}
'''

MUTANTS = {
    "해시 다름인데 덮어씀": [(OVERWRITE_OLD, OVERWRITE_NEW)],
    "지문 비교 생략 (늘 같다고 함)": [("if stored_fp is not None and fp.same(stored_fp, new):  # MUT:compare", "if True:  # MUT:compare")],
    "의도 없이 바로 씀": [("    save_item(ctx, item)  # 2. 의도 (MUT:intent)", "    pass  # 2. (의도를 건너뜀)")],
    "edit이 validate를 건너뜀": [("    verr = next((a[\"errors\"] for a in v[\"adrs\"] if a[\"id\"] == nid), [\"validate에 없음\"])", "    verr = []")],
    "edit으로 status를 바꿀 수 있음": [('EDIT_FORBIDDEN = ("status", "id", "number", "adr", "adr_id")', "EDIT_FORBIDDEN = ()"),
                                     ('errs += [f"모르는 칸: {k}" for k in edited if k not in AdrDraft.model_fields and k not in EDIT_FORBIDDEN]', "pass")],
    "번호를 남이 가져가도 다시 안 정함": [("    if taken:  # 2와 3 사이", "    if False:  # 2와 3 사이")],
    "이미 의도가 있어도 새로 씀 (이중 쓰기)": [('    if item and item.get("write_intent"):\n        if item["write_intent"]["state"] == "done":', '    if False:\n        if item["write_intent"]["state"] == "done":')],
}


def load_mutant(patches, tag):
    src = SRC
    for old, new in patches:
        assert src.count(old) == 1, f"변이가 소스에 안 맞는다(헛돌 수 있다): {old[:60]!r} × {src.count(old)}"
        src = src.replace(old, new)
    tmp = os.path.join(tempfile.mkdtemp(prefix="jhs-mut-"), f"hitl_mut_{tag}.py")
    open(tmp, "w", encoding="utf-8", newline="\n").write(src)
    spec = importlib.util.spec_from_file_location(f"radar.hitl_mut_{tag}", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 2~4. 그래프 (스텁 모델로 capture 2턴) ────────────────────────────

def turn1(w, script, conv="대화 하나", fingerprint=None, item_id=None):
    ctx = w.ctx()
    return asyncio.run(capture.capture_turn1(ctx, StubChat(script=script, flow="capture"), conv, NOW, w.state, "stub",
                                             fingerprint=fingerprint, item_id=item_id))


def turn2(w, script, decision, **kw):
    ctx = w.ctx()
    rep, diag = capture.capture_turn2(ctx, StubChat(script=script, flow="capture"), w.state, decision, NOW, "stub", **kw)
    return rep, ctx


def pending(w, script, item_id):
    """멈춘 대화에 남은 대기 승인 수 (처음엔 LangGraph 체크포인트의 interrupts — ADR 0030 뒤 hitl.ThreadStore)."""
    t = hitl.ThreadStore(w.state).get(item_id)
    return len([c for c in (t or {}).get("pending") or [] if c["name"] in capture.GATED])


def thread_exists(w, item_id):
    return hitl.ThreadStore(w.state).get(item_id) is not None


def graph_tests():
    print("2. 그래프 — item-id · R9 · 승인 · 거부 · 정리")
    w = World()
    before = w.snapshot()
    rep, _ = turn1(w, KOPII)
    iid = rep["item_id"]
    check("thread_id = item-id (\"capture\" 고정이 아니다)", iid.startswith("2026-09-28-capture-") and iid != "capture", iid)
    check("R9 — 승인 전 ADR repo 쓰기 0 (파일 하나도 안 바뀌고, .radar도 없다)", w.snapshot() == before and not os.path.exists(os.path.join(w.root, ".radar")))
    check("승인 대기 + 지문이 checkpoint 파일에 남았다", rep["awaiting_approval"] and hitl.StateStore(w.state).get(iid)[0]["impl"] == hitl.DEFAULT_FINGERPRINT == "all")  # 결정 7 — 기본은 A′
    rep2, _ = turn2(w, KOPII, "approve")
    it = w.item(iid)
    check("승인 순간에 별도 파일이 생겼고 ADR이 써졌다 (0006, validate 통과)", it["write_intent"]["state"] == "done" and it["adr"] == "0006" and "0006-kopii-small.md" in w.adrs(), it["write_intent"])
    check("별도 파일에 status 칸이 없다 (R8)", "status" not in it)
    check("끝나면 checkpoint thread와 지문을 지운다", not thread_exists(w, iid) and hitl.StateStore(w.state).threads() == [])
    check("보고서에 files_written 1건", len(rep2["files_written"]) == 1, rep2["files_written"])
    w.cleanup()

    w = World()
    before = w.snapshot()
    rep, _ = turn1(w, KOPII)
    iid = rep["item_id"]
    rep2, _ = turn2(w, KOPII, "reject")
    check("거부 — 쓰기 0 (별도 파일도 없다, R9) · thread 정리", w.snapshot() == before and not thread_exists(w, iid))
    w.cleanup()

    # S3 — 승인 뒤 ADR을 쓰고 완료를 적기 전에 프로세스가 죽는다 → 재개가 같은 ADR을 또 쓰지 않는다
    print("3. 실제 그래프에서 쓰다 죽기 (S3)")
    w = World()
    rep, _ = turn1(w, KOPII)
    iid = rep["item_id"]
    real_complete = hitl._complete
    hitl._complete = lambda *a, **k: (_ for _ in ()).throw(Crash("complete 전"))
    try:
        died = False
        try:
            turn2(w, KOPII, "approve")
        except Exception as e:
            died = "Crash" in repr(e) or isinstance(e, Crash)
    finally:
        hitl._complete = real_complete
    mid = w.item(iid)["write_intent"]["state"], [a for a in w.adrs() if "kopii" in a]
    rep2, _ = turn2(w, KOPII, "approve")
    kopii = [a for a in w.adrs() if "kopii" in a]
    check("죽은 뒤 상태: 의도 intended + ADR 파일 있음", died and mid == ("intended", ["0006-kopii-small.md"]), mid)
    check("재개 → 회복(completed), ADR은 하나, thread 정리", kopii == ["0006-kopii-small.md"] and w.item(iid)["write_intent"]["state"] == "done"
          and rep2["hitl"]["recovery"] == "completed" and not thread_exists(w, iid), (kopii, rep2.get("hitl")))
    w.cleanup()

    # 낡음 — S1·S4 × 지문 (A)·(B)
    print("4. 낡음 (승인 대기 중 ADR repo가 바뀜) — 지문 (A) repo_hash · (B) related_match")
    for impl in ("repo_hash", "related_match"):
        for scen in ("S4 관련 ADR 0003 status 변경", "S1 같은 기술 ADR이 새로 생김"):
            w = World()
            rep, _ = turn1(w, KOPII, fingerprint=impl)
            iid = rep["item_id"]
            if scen.startswith("S4"):
                p = os.path.join(w.adr, "0003-guardrails-pii.md")
                sub_file(p, "status: adopted", "status: rejected")
            else:
                open(os.path.join(w.adr, "0006-kopii-small.md"), "w", encoding="utf-8", newline="\n").write(ops.render(D1, NOW))
            snap = w.snapshot()
            rep2, ctx2 = turn2(w, KOPII, "approve")
            st = (rep2.get("hitl") or {}).get("stale")
            check(f"[{impl}] {scen} → 재개하지 않고 다시 interrupt + 변경 요약",
                  st and rep2["awaiting_approval"] and st["changes"] and pending(w, KOPII, iid) == 1 and w.snapshot() == snap and not w.items(), rep2.get("hitl"))
            if impl == "repo_hash" and scen.startswith("S4"):
                check("  요약에 무엇이 바뀌었는지 (ADR 0003 status adopted → rejected)", any("0003" in c and "adopted" in c and "rejected" in c for c in st["changes"]), st["changes"])
            rep3, _ = turn2(w, KOPII, "approve", stale_answer="continue")
            n_new = len([a for a in w.adrs() if "kopii" in a])
            check("  계속 → 그대로 승인해 쓴다 (사람이 알고 고른 것)", not rep3["awaiting_approval"] and w.item(iid)["write_intent"]["state"] == "done"
                  and n_new == (2 if scen.startswith("S1") else 1), w.adrs())
            w.cleanup()
        # 다시 판정 · 버림
        for ans in ("discard", "rejudge"):
            w = World()
            rep, _ = turn1(w, KOPII, fingerprint=impl)
            iid = rep["item_id"]
            p = os.path.join(w.adr, "0003-guardrails-pii.md")
            sub_file(p, "status: adopted", "status: hold")
            snap = w.snapshot()
            rep2, _ = turn2(w, KOPII, "approve", stale_answer=ans)
            check(f"[{impl}] stale 답 {ans} → 아무것도 안 쓰고 thread 정리", w.snapshot() == snap and not thread_exists(w, iid)
                  and rep2["hitl"]["outcome"] == ans and not rep2["awaiting_approval"], rep2.get("hitl"))
            w.cleanup()
    # (A)와 (B)의 차이 — 무관한 ADR(0005) 변경
    for impl, expect_stale in (("repo_hash", True), ("related_match", False)):
        w = World()
        rep, _ = turn1(w, KOPII, fingerprint=impl)
        p = os.path.join(w.adr, "0005-faiss-ivf.md")
        open(p, "a", encoding="utf-8", newline="\n").write("\n사람이 덧붙임\n")
        rep2, _ = turn2(w, KOPII, "approve")
        stale = bool((rep2.get("hitl") or {}).get("stale"))
        check(f"[{impl}] 무관한 ADR(0005) 변경 → {'다시 묻는다' if expect_stale else '다시 묻지 않는다'}", stale == expect_stale, rep2.get("hitl"))
        w.cleanup()

    # S2 — 두 항목 동시 대기
    print("5. S2 — 두 항목이 동시에 대기")
    for impl in ("repo_hash", "related_match"):
        w = World()
        d2 = draft2()
        sc2 = script_for(d2)
        rA, _ = turn1(w, KOPII, conv="대화 A", fingerprint=impl)
        rB, _ = turn1(w, sc2, conv="대화 B", fingerprint=impl)
        rA2, _ = turn1(w, KOPII, conv="대화 A", fingerprint=impl)  # 같은 대화를 또 넣어도 대기 중인 항목을 덮지 않는다
        ids = [rA["item_id"], rB["item_id"], rA2["item_id"]]
        check(f"[{impl}] item-id가 서로 다르다 (같은 대화를 또 넣어도)", len(set(ids)) == 3, ids)
        check(f"[{impl}] 셋 다 대기 중", all(pending(w, s, i) == 1 for s, i in ((KOPII, ids[0]), (sc2, ids[1]), (KOPII, ids[2]))))
        try:
            turn2(w, KOPII, "approve")
            ambiguous = False
        except hitl.HitlError:
            ambiguous = True
        check(f"[{impl}] item을 안 주면 어느 것인지 모르니 멈춘다", ambiguous)
        turn2(w, KOPII, "approve", item_id=ids[0])
        # 첫 항목이 ADR을 쓰면 repo가 바뀐다 — (A)는 다른 항목도 다시 묻는다, (B)는 무관하니 묻지 않는다
        rB2, _ = turn2(w, sc2, "approve", item_id=ids[1])
        asked = bool((rB2.get("hitl") or {}).get("stale"))
        if asked:
            rB2, _ = turn2(w, sc2, "approve", item_id=ids[1], stale_answer="continue")
        check(f"[{impl}] 첫 항목 뒤 둘째는 {'다시 물었고(A) 계속으로' if asked else '묻지 않고'} 승인", asked == (impl == "repo_hash"), rB2.get("hitl"))
        a, b = w.item(ids[0])["write_intent"], w.item(ids[1])["write_intent"]
        check(f"[{impl}] 서로 안 덮었다 — 다른 번호·다른 파일, 별도 파일 둘", a["adr"] != b["adr"] and a["path"] != b["path"] and len(w.items()) == 2
              and w.adrs().count(os.path.basename(a["path"])) == 1 and w.adrs().count(os.path.basename(b["path"])) == 1, (a, b))
        check(f"[{impl}] 셋째(남은 대기)는 그대로 대기", pending(w, KOPII, ids[2]) == 1 and not thread_exists(w, ids[0]) and thread_exists(w, ids[2]))
        w.cleanup()

    # edit
    print("6. edit")
    d = json.loads(json.dumps(KOPII["draft"]))
    w = World()
    rep, _ = turn1(w, KOPII)
    iid = rep["item_id"]
    orig_hash = hitl.sha(ops.render(D1, NOW))
    snap = w.snapshot()
    # 6a validate 실패 — 쓰지 않고 오류를 돌려준다, 승인 대기는 그대로
    bad = {**d, "research_note": ["blog.devnews.example/kopii-review — 후기. 근거가 아니다"]}
    rb, _ = turn2(w, KOPII, "edit", edited=bad)
    check("validate에 걸리는 edit → 쓰지 않는다 · 오류를 돌려준다 · 승인 대기 유지", w.snapshot() == snap and rb["awaiting_approval"]
          and any("R17" in e for e in rb["hitl"]["edit_errors"]) and pending(w, KOPII, iid) == 1 and not w.items(), rb.get("hitl"))
    # 6b status·번호는 못 바꾼다
    rs, _ = turn2(w, KOPII, "edit", edited={**d, "status": FORGED})
    rn, _ = turn2(w, KOPII, "edit", edited={**d, "id": "0099"})
    check("edit으로 status·번호를 바꾸려 하면 오류 — 쓰지 않는다", w.snapshot() == snap and "edit_errors" in rs["hitl"] and "edit_errors" in rn["hitl"], (rs.get("hitl"), rn.get("hitl")))
    # 6c 고쳐서 다시 — 같은 대기에서 성공
    good = {**d, "decision": "제안 — 사람이 고친 결정문. 채택 여부는 사람이 정한다."}
    rg, _ = turn2(w, KOPII, "edit", edited=good)
    text = w.adr_text("0006-kopii-small.md")
    it = w.item(iid)
    ev = it["history"][0]
    check("고친 초안이 저장됐다 (status는 proposed, 번호 0006)", "사람이 고친 결정문" in text and "\nstatus: proposed\n" in text and it["adr"] == "0006")
    check("의도의 content_hash = 고친 초안 해시, history에 원래·고친 해시 둘 다", it["write_intent"]["content_hash"] == hitl.sha(text) == ev["draft_hash_edited"]
          and ev["draft_hash_original"] == orig_hash and ev["decision"] == "edit", ev)
    check("edit 뒤에도 thread 정리", not thread_exists(w, iid))
    w.cleanup()

    # 실제 새 프로세스로 edit·stale 답 (resume.py CLI)
    print("7. 새 프로세스(resume.py) — edit · stale 답 · item 생략")
    w = World()
    rep, _ = turn1(w, KOPII)
    iid = rep["item_id"]
    sf = os.path.join(w.dir, "tmp", "stub.json")
    json.dump(KOPII, open(sf, "w", encoding="utf-8"), ensure_ascii=False)
    ef = os.path.join(w.dir, "tmp", "edit.json")
    json.dump(good, open(ef, "w", encoding="utf-8"), ensure_ascii=False)
    p3 = os.path.join(w.adr, "0003-guardrails-pii.md")
    sub_file(p3, "status: adopted", "status: rejected")

    def cli(out, *extra):
        os.makedirs(out, exist_ok=True)
        p = subprocess.run([sys.executable, os.path.join(HERE, "resume.py"), "--state", w.state, "--decision", "edit", "--now", NOW, "--radar", SKILL_RADAR,
                            "--out", out, "--stub-script", sf, "--edit-file", ef, *extra], env=w.env(), capture_output=True, text=True, encoding="utf-8")
        ev = [json.loads(l) for l in open(os.path.join(out, "transcript-2.jsonl"), encoding="utf-8")]
        rep = rc.extract_report(next(e for e in reversed(ev) if e.get("type") == "result")["result"])
        return p.returncode, rep, json.load(open(os.path.join(out, "resume.json"), encoding="utf-8"))

    rc1, r1, j1 = cli(os.path.join(w.dir, "o1"))
    check("새 프로세스 재개 — 낡음이면 쓰지 않고 다시 interrupt (item 생략: 대기가 하나)", rc1 == 0 and r1["awaiting_approval"] and r1["hitl"]["stale"]["changes"] and not w.items(), r1)
    rc2, r2, j2 = cli(os.path.join(w.dir, "o2"), "--stale-answer", "continue")
    check("새 프로세스 재개 — 계속 + edit → 고친 초안이 써졌다", rc2 == 0 and "사람이 고친 결정문" in w.adr_text("0006-kopii-small.md") and w.item(iid)["adr"] == "0006"
          and j1["pid"] != os.getpid(), (rc2, r2))
    w.cleanup()

    # 실제 프로세스를 죽이기 — write-ahead 단계 사이에서 os._exit
    print("8. 실제 프로세스 kill (os._exit) — write-ahead 단계 사이")
    for step, want in (("number", "none"), ("intent", "resumed_write"), ("adr", "completed")):
        w = World()
        p = subprocess.run([sys.executable, os.path.abspath(__file__), "--child", step, w.dir], env=w.env(), capture_output=True, text=True, encoding="utf-8")
        rec = hitl.recover(w.ctx(), "it-1")
        kopii = [a for a in w.adrs() if "kopii" in a]
        if step == "number":
            hitl.write_adr(w.ctx(), "it-1", "capture", D1)
            kopii = [a for a in w.adrs() if "kopii" in a]
        check(f"'{step}' 뒤에 프로세스가 죽음(exit 77) → 회복 {want}, ADR은 하나", p.returncode == 77 and rec["action"] == want and kopii == ["0006-kopii-small.md"], (p.returncode, rec["action"], kopii))
        w.cleanup()


def child(step, wdir):
    """자식 프로세스 — write-ahead 중 step 뒤에서 프로세스를 끝낸다 (파일은 닫힌 채)."""
    w = World.__new__(World)
    w.dir = wdir
    w.root = os.path.join(wdir, "adr-fixture")
    w.adr = os.path.join(w.root, "docs", "adr")
    hitl.write_adr(w.ctx(), "it-1", "capture", D1, after=lambda s: os._exit(77) if s == step else None)
    sys.exit(0)


def main():
    print("1. 회복 · 별도 파일 · edit 검사 (함수 단위)")
    res = run_unit(hitl)
    for name, ok, detail in res:
        check(name, ok, detail)

    graph_tests()

    print("9. 변이 — 일부러 틀리게 고친 hitl.py를 위 검사가 잡는다 (규율 3)")
    for i, (label, patches) in enumerate(MUTANTS.items()):
        mod = load_mutant(patches, i)
        caught = [n for n, ok, _ in run_unit(mod) if not ok]
        check(f"변이 「{label}」 → 잡힘", bool(caught), "잡히지 않았다 — 검사가 무력하다")
        if caught:
            print(f"       잡은 검사: {caught}")
    # 그래프 수준 변이: 지문 비교를 생략하면 낡은 것을 그대로 승인한다 — 4번의 그래프 검사가 잡는지
    mod = load_mutant(MUTANTS["지문 비교 생략 (늘 같다고 함)"], "g")
    real = hitl.check_resume
    hitl.check_resume = mod.check_resume
    try:
        w = World()
        rep, _ = turn1(w, KOPII)
        p = os.path.join(w.adr, "0003-guardrails-pii.md")
        sub_file(p, "status: adopted", "status: rejected")
        rep2, _ = turn2(w, KOPII, "approve")
        check("그래프 변이: 비교를 생략하면 S4를 못 잡고 그대로 쓴다 → 4번 검사가 이를 잡는다", not (rep2.get("hitl") or {}).get("stale") and bool(w.items()),
              "비교 생략이 그래프에서도 눈에 안 띈다")
        w.cleanup()
    finally:
        hitl.check_resume = real

    print(f"\n{'FAILED ' + str(len(fails)) + ': ' + ', '.join(fails) if fails else 'all passed'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
    main()

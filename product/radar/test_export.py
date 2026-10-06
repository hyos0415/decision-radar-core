"""지문 A′ · git 내보내기 회귀 — 네트워크·실제 API 없이 로컬 임시 repo + 로컬 bare remote로만 돈다 (설계 docs/design/hitl-state.md 결정 7·8, HYO-44).

  PYTHONIOENCODING=utf-8 .venv/Scripts/python product/radar/test_export.py

GitHub에는 닿지 않는다 — 원격은 임시 폴더의 `git init --bare`다. 전역 git 설정도 끈다(GIT_CONFIG_GLOBAL=NUL).

1. 지문 A′   기록용 칸(radar.py가 실제로 쓰는 `write_updates`)만 바뀌면 같다 · status·새/지운 ADR·재검토 조건·본문·대상 repo 스택 절이 바뀌면 다르다
             · 차이 요약 · 스위치 RADAR_FINGERPRINT=related(B′) · collector의 스택 절 파서와 같은 이름이 나오나
2. 그래프     capture 2턴에서 A′가 기본이고 revisit routine이 쓰는 칸은 다시 묻지 않는다 · 스위치 · 내보내기(승인 → 커밋 1개) · 원격이 앞섰을 때
3. 내보내기   허용 목록 · 기존 ADR 수정 불가 · ff-only · 원격이 앞섰을 때 재개 검사 다시(같으면 커밋, 다르면 interrupt) · push 경합 거부 후 회복
             · detect 검토(approve/reject) · review.py 새 프로세스 · eval·dry-run에서는 git을 안 부른다
4. 변이       일부러 틀리게 고친 hitl.py·export.py를 위 검사가 잡나 (규율 3 — 통과는 증거가 아니다)
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types

sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # product/

# 전역·시스템 git 설정(서명·autocrlf·훅 …)이 테스트를 흔들지 않게 한다. 이 프로세스가 부르는 모든 git(내보내기 포함)에 적용된다
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

import yaml  # noqa: E402

from radar import capture, hitl, test_hitl as th  # noqa: E402
from radar import export as export_mod  # noqa: E402
from common import ops  # noqa: E402
from common.core import SKILL_RADAR  # noqa: E402
from common.schemas import AdrDraft  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
NOW = th.NOW
D1, KOPII = th.D1, th.KOPII
fails = []
IDENT = {"name": "decision-radar", "email": "radar@example.invalid"}


def check(name, ok, detail=None):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok or detail is None else f" — {detail}"))
    if not ok:
        fails.append(name)


# ── git 도우미 ───────────────────────────────────────────────────────

def git(cwd, *args, check_rc=True):
    p = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "LC_ALL": "C"})
    if check_rc and p.returncode:
        raise RuntimeError(f"git {' '.join(args)} → {p.returncode}: {p.stderr.strip()[:200]}")
    return p.stdout.strip()


def _cfg(d):
    git(d, "config", "user.name", "human")
    git(d, "config", "user.email", "human@example.invalid")
    git(d, "config", "commit.gpgsign", "false")


STACK_MD = """# {name}

## 스택
- `chat-api`: FastAPI

## 스택 · 평가셋

### 의존성 → component

| 의존성 | component |
|---|---|
| fastapi · pydantic | chat-api |
| boto3 | pii-guard |

### 외부 모델·서비스

| 모델·서비스 | component |
|---|---|
| Anthropic `claude-sonnet-5-5` | eval |

### 평가셋

- 회귀 53케이스
"""

_TPL = []


def template():
    """git 세계의 원형 — 한 번만 만들고 시나리오마다 복사한다 (원격 URL만 다시 건다)."""
    if _TPL:
        return _TPL[0]
    d = tempfile.mkdtemp(prefix="jhs-gtpl-")
    root = os.path.join(d, "adr-fixture")
    shutil.copytree(th.FIXTURE, root)
    os.makedirs(os.path.join(d, "tmp"))
    remote = os.path.join(d, "remote.git")
    git(d, "init", "--bare", "-q", "-b", "main", remote)
    git(root, "init", "-q", "-b", "main")
    _cfg(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    git(root, "remote", "add", "origin", remote)
    git(root, "push", "-q", "-u", "origin", "main")
    git(d, "clone", "-q", remote, "human")
    _cfg(os.path.join(d, "human"))
    targets = []
    for name in ("medical-mate", "finance-verifier"):
        os.makedirs(os.path.join(d, "targets", name))
        open(os.path.join(d, "targets", name, "CLAUDE.md"), "w", encoding="utf-8", newline="\n").write(STACK_MD.format(name=name))
        targets.append({"repo": name, "path": f"targets/{name}"})
    json.dump(targets, open(os.path.join(d, "targets.json"), "w", encoding="utf-8"))
    _TPL.append(d)
    return d


class GWorld(th.World):
    """git 세계 — ADR repo 작업 사본(root) · 로컬 bare remote · 「사람」의 clone · 대상 repo(CLAUDE.md) 둘."""

    def __init__(self, dry=False, extra_env=None):
        tpl = template()
        self.dir = tempfile.mkdtemp(prefix="jhs-hitl-g-")
        shutil.rmtree(self.dir)
        shutil.copytree(tpl, self.dir)
        self.root = os.path.join(self.dir, "adr-fixture")
        self.adr = os.path.join(self.root, "docs", "adr")
        self.state = os.path.join(self.dir, "tmp", "capture-checkpoint.sqlite")
        self.remote = os.path.join(self.dir, "remote.git")
        self.human = os.path.join(self.dir, "human")
        for d in (self.root, self.human):
            git(d, "remote", "set-url", "origin", self.remote)
        self.dry, self.extra_env = dry, extra_env or {}
        self.base = git(self.root, "rev-parse", "HEAD")

    def env(self):
        e = super().env()
        e.update(RADAR_DRY_RUN="1" if self.dry else "0", RADAR_EXPORT="git", RADAR_TARGETS=os.path.join(self.dir, "targets.json"),
                 RADAR_GIT_NAME=IDENT["name"], RADAR_GIT_EMAIL=IDENT["email"])
        e.update(self.extra_env)
        return e

    def ex(self, X=export_mod, **kw):
        return X.GitExporter(self.root, identity=IDENT, **kw)

    def target_md(self, repo):
        return os.path.join(self.dir, "targets", repo, "CLAUDE.md")

    # 사람이 다른 clone에서 push한다
    def human_push(self, fn, msg="사람의 변경"):
        git(self.human, "pull", "-q", "--ff-only")
        fn(self.human)
        git(self.human, "add", "-A")
        git(self.human, "commit", "-q", "-m", msg)
        git(self.human, "push", "-q", "origin", "main")

    def lhead(self):
        return git(self.root, "rev-parse", "HEAD")

    def rhead(self):
        return git(self.remote, "rev-parse", "main")

    def count(self, rng=None):
        return int(git(self.remote, "rev-list", "--count", rng or f"{self.base}..main"))

    def subjects(self):
        return git(self.remote, "log", "--format=%s", f"{self.base}..main").splitlines()[::-1]  # 오래된 것부터

    def commit_files(self, sha):
        out = git(self.remote, "diff-tree", "--no-commit-id", "--name-status", "-r", "--root", sha)
        return {l.split("\t")[1]: l.split("\t")[0] for l in out.splitlines() if l}

    def remote_show(self, rel):
        return git(self.remote, "show", f"main:{rel}", check_rc=False)

    def staged(self):
        return git(self.root, "diff", "--cached", "--name-only")


_spec = importlib.util.spec_from_file_location("radar_py_under_test", SKILL_RADAR)
RADARPY = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RADARPY)


def touch_machine(adr_dir, aid="0004", result="not_met", at="2026-10-13T09:07:00+09:00"):
    """매주 월요일 revisit routine이 하는 일 — radar.py의 실제 write_updates로 revisit_last_checked를 갱신한다 (yaml 다시 쓰기 포함)."""
    path = [os.path.join(adr_dir, f) for f in os.listdir(adr_dir) if f.startswith(aid)][0]
    before = open(path, "rb").read()
    RADARPY.write_updates(adr_dir, {aid: [{"condition_id": "c1", "checked_at": at, "result": result, "reason": None,
                                           "evidence_urls": ["x.example/releases"], "last_determined": result}]})
    assert open(path, "rb").read() != before, "기록용 칸을 못 바꿨다 — 테스트가 헛돈다"


def adr_path(w, prefix):
    return os.path.join(w.adr, [f for f in os.listdir(w.adr) if f.startswith(prefix)][0])


def draft2():
    return AdrDraft.model_validate(th.draft2())


def approve(w, h, ex, draft=D1, item="it-1", baseline=None, hold=False, kind="capture", run_id=None):
    """capture 승인 한 번 — 멈출 때(또는 재개 직전)의 지문을 근거로 쓰고 내보낸다 (capture_turn2가 하는 일)."""
    ctx = w.ctx()
    fp = baseline or h.fingerprint_for(ctx).compute(ctx, h.scope_of(draft))
    if hold:
        return h.write_adr(ctx, item, kind, draft, lifecycle="REVIEW_CANDIDATE", final_lifecycle="REVIEW_CANDIDATE", by="graph", exporter=ex, hold_export=True, run_id=run_id)
    return h.write_adr(ctx, item, kind, draft, approval={"decision": "approve", "fingerprint": fp}, exporter=ex, run_id=run_id)


def baseline(w, h, draft=D1, name=None):
    ctx = w.ctx()
    return h.fingerprint_for(ctx, name).compute(ctx, h.scope_of(draft))


# ── 시나리오 ─────────────────────────────────────────────────────────
# 시나리오는 (h, X, w)를 받는다 — h는 hitl 모듈이거나 변이본, X는 export 모듈이거나 변이본

SCEN = {}


def scen(f):
    SCEN[f.__name__] = f
    return f


def verdict(h, w, old, name="all", draft=D1):
    ctx = w.ctx()
    fp = h.FINGERPRINTS[name]
    new = fp.compute(ctx, h.scope_of(draft))
    return fp.same(old, new), fp.diff(old, new)


# 1. 지문 A′ ──────────────────────────────────────────────────────────

@scen
def f_machine_fields_only(h, X, w):
    """기록용 칸(revisit_last_checked)만 바뀌면 같다 — radar.py의 실제 쓰기(yaml 다시 쓰기 포함). (A)는 헛경보를 내던 것"""
    old, old_a, old_r = baseline(w, h), baseline(w, h, name="repo_hash"), baseline(w, h, name="related")
    touch_machine(w.adr, "0004")
    touch_machine(w.adr, "0004", result="met", at="2026-10-20T09:07:00+09:00")  # 같은 칸을 또 바꿔도
    s_all, d_all = verdict(h, w, old)
    s_rel, _ = verdict(h, w, old_r, "related")
    s_a, _ = verdict(h, w, old_a, "repo_hash")
    return s_all and s_rel and not d_all and not s_a, {"all": (s_all, d_all), "related": s_rel, "legacy_A_same": s_a}


@scen
def f_status_change(h, X, w):
    old = baseline(w, h)
    th.sub_file(adr_path(w, "0004"), "status: hold", "status: rejected")
    same, diff = verdict(h, w, old)
    return not same and any("0004" in l and "status: hold → rejected" in l for l in diff), diff


@scen
def f_new_and_removed_adr(h, X, w):
    old = baseline(w, h)
    open(os.path.join(w.adr, "0006-kopii-small.md"), "w", encoding="utf-8", newline="\n").write(ops.render(D1, NOW))
    s1, d1 = verdict(h, w, old)
    os.remove(os.path.join(w.adr, "0006-kopii-small.md"))
    os.remove(adr_path(w, "0005"))
    s2, d2 = verdict(h, w, old)
    return (not s1 and any("새 ADR 0006-kopii-small.md" in l and "KoPII-small" in l and "proposed" in l for l in d1)
            and not s2 and any("0005" in l and "사라졌다" in l for l in d2)), (d1, d2)


@scen
def f_condition_body_and_nested_machine_key(h, X, w):
    """재검토 조건 본문·본문 텍스트가 바뀌면 다르고, 조건 안에 끼어든 last_determined는 기록용이라 같다"""
    old = baseline(w, h)
    p = adr_path(w, "0004")
    orig = open(p, encoding="utf-8", newline="").read()
    th.sub_file(p, "    source_hint: github.example/leann/releases\n", "    source_hint: github.example/leann/releases\n    last_determined: met\n")
    s_nested, d_nested = verdict(h, w, old)
    open(p, "w", encoding="utf-8", newline="").write(orig)
    th.sub_file(p, "incremental insert를 공식 지원한다", "incremental insert와 delete를 공식 지원한다")
    s_cond, d_cond = verdict(h, w, old)
    open(p, "w", encoding="utf-8", newline="").write(orig + "\n사람이 덧붙인 문장\n")
    s_body, d_body = verdict(h, w, old)
    return (s_nested and not s_cond and any("재검토 조건 c1 바뀜" in l for l in d_cond)
            and not s_body and any("본문이 바뀌었다" in l for l in d_body)), (d_nested, d_cond, d_body)


@scen
def f_stack_sections(h, X, w):
    """대상 repo CLAUDE.md의 스택 절 — 의존성·외부 모델 표가 바뀌면 다르다. 다른 절·공백·CRLF만 바뀌면 같다. 파일이 사라지면 요약에 나온다"""
    old = baseline(w, h)
    p = w.target_md("medical-mate")
    orig = open(p, encoding="utf-8", newline="").read()

    def put(text):
        open(p, "w", encoding="utf-8", newline="").write(text)

    put(orig.replace("| boto3 | pii-guard |", "| boto3 | pii-guard |\n| httpx | chat-api |"))
    s_dep, d_dep = verdict(h, w, old)
    put(orig.replace("| Anthropic `claude-sonnet-5-5` | eval |", "| Anthropic `claude-sonnet-5-6` | eval |"))
    s_svc, d_svc = verdict(h, w, old)
    put(orig.replace("회귀 53케이스", "회귀 100케이스"))
    s_other, _ = verdict(h, w, old)
    put(orig.replace("| boto3 | pii-guard |", "|  boto3   |   pii-guard |").replace("\n", "\r\n"))
    s_ws, _ = verdict(h, w, old)
    os.remove(p)
    s_gone, d_gone = verdict(h, w, old)
    return (not s_dep and any("medical-mate" in l and "의존성 → component" in l and "추가" in l and "httpx" in l for l in d_dep)
            and not s_svc and any("medical-mate" in l and "외부 모델·서비스" in l for l in d_svc)
            and s_other and s_ws
            and not s_gone and any("medical-mate" in l and "사라졌다" in l for l in d_gone)), (d_dep, d_svc, d_gone)


@scen
def f_switch_related(h, X, w):
    """스위치 — 기본은 A′(all), RADAR_FINGERPRINT=related면 B′. B′는 무관한 변화·무관한 repo의 스택은 안 묻고, 관련 ADR·가리키는 repo의 스택은 묻는다"""
    ctx = w.ctx()
    dflt = h.fingerprint_for(ctx).name
    w.extra_env["RADAR_FINGERPRINT"] = "related"
    sel = h.fingerprint_for(w.ctx()).name
    w.extra_env["RADAR_FINGERPRINT"] = "bogus"
    try:
        h.fingerprint_for(w.ctx())
        bogus = False
    except h.HitlError:
        bogus = True
    w.extra_env.pop("RADAR_FINGERPRINT")
    old_r, old_a = baseline(w, h, name="related"), baseline(w, h, name="all")
    open(adr_path(w, "0005"), "a", encoding="utf-8", newline="\n").write("\n무관한 변화\n")
    r_unrel, _ = verdict(h, w, old_r, "related")
    a_unrel, _ = verdict(h, w, old_a, "all")
    fv = w.target_md("finance-verifier")
    open(fv, "a", encoding="utf-8", newline="\n").write("\n### 의존성 → component\n| httpx | verifier-core |\n")
    r_other_stack, _ = verdict(h, w, old_r, "related")
    a_other_stack, _ = verdict(h, w, old_a, "all")
    mm = w.target_md("medical-mate")
    th.sub_file(mm, "| boto3 | pii-guard |", "| boto3 | pii-guard |\n| httpx | chat-api |")
    r_own_stack, d_own = verdict(h, w, old_r, "related")
    th.sub_file(adr_path(w, "0003"), "status: adopted", "status: rejected")
    r_rel, d_rel = verdict(h, w, old_r, "related")
    return (dflt == "all" and sel == "related" and bogus
            and r_unrel and not a_unrel
            and r_other_stack and not a_other_stack
            and not r_own_stack and any("medical-mate" in l for l in d_own)
            and not r_rel and any("0003" in l and "adopted → rejected" in l for l in d_rel)), (dflt, sel, bogus, r_unrel, a_unrel, r_other_stack, a_other_stack, d_own, d_rel)


@scen
def f_parser_parity_with_collector(h, X, w):
    """스택 절 파서가 collector의 파서와 같은 이름을 뽑는가 — 이 레포 CLAUDE.md로 맞춰 본다 (collector를 못 불러오면 건너뛴다)"""
    text = open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8").read()
    rows = h.stack_rows(text)
    first = lambda r: r.strip("|").split("|")[0].strip()
    mine_services = {first(r) for r in rows["services"] if not first(r).startswith("모델·서비스")}
    mine_deps_text = " ".join(rows["deps"])
    if not rows["services"] or not rows["deps"]:
        return False, "이 레포 CLAUDE.md에서 스택 절을 못 찾았다"
    sys.path.insert(0, ROOT)
    try:
        from collector.sources import github, vendor
    except Exception as e:  # noqa: BLE001
        return True, f"건너뜀 — collector를 못 불러온다: {type(e).__name__}"
    finally:
        sys.path.remove(ROOT)
    theirs_services = set(vendor.services_from_claude_md(text))
    theirs_deps = github.dependencies_from_claude_md(text)
    return (mine_services == theirs_services and theirs_deps and all(d in mine_deps_text for d in theirs_deps)), (mine_services ^ theirs_services, theirs_deps)


# 3. 내보내기 ─────────────────────────────────────────────────────────

@scen
def e_capture_export_one_commit(h, X, w):
    """승인 → 커밋 1개(ADR + 별도 파일만, 메시지에 item-id·ADR 번호) → main에 push"""
    ex = w.ex(X)
    r = approve(w, h, ex)
    sha = w.rhead()
    files = w.commit_files(sha)
    msg = git(w.remote, "log", "-1", "--format=%B", sha)
    it = w.item("it-1")
    committed_item = yaml.safe_load(w.remote_show(".radar/items/it-1.yaml"))
    return (r["export"]["state"] == "exported" and w.count() == 1 and w.lhead() == w.rhead()
            and files == {"docs/adr/0006-kopii-small.md": "A", ".radar/items/it-1.yaml": "A"}
            and "it-1" in msg and "ADR 0006" in msg
            and git(w.remote, "log", "-1", "--format=%an", sha) == IDENT["name"]
            and it["lifecycle"] == "DONE" and it["export"] == {"state": "done"} and "attention" not in it
            and committed_item["export"] == {"state": "done"} and committed_item["lifecycle"] == "DONE"
            and w.remote_show("docs/adr/0006-kopii-small.md") == w.adr_text("0006-kopii-small.md").rstrip("\n")
            and git(w.root, "status", "--porcelain") == ""), (r.get("export"), files, msg)


@scen
def e_allowlist(h, X, w):
    """경로 허용 목록 — 허용 밖 경로는 커밋하지 않는다. 같이 스테이징된 변경이 있으면 거부. 허용 밖 파일은 커밋에 안 딸려 간다"""
    ex = w.ex(X)
    head = w.lhead()
    open(os.path.join(w.root, "notes.txt"), "w", encoding="utf-8").write("메모")
    r1 = ex.publish(["notes.txt"], "허용 밖")
    r1b = ex.publish(["docs/adr/notes.md"], "번호 없는 ADR 이름")
    r1c = ex.publish([".radar/../notes.txt"], "상위 경로")
    ok1 = all(r["state"] == "blocked" for r in (r1, r1b, r1c)) and w.lhead() == head and w.rhead() == head
    # 같이 스테이징된 변경
    th.sub_file(os.path.join(w.root, "radar-targets.json"), "medical-mate", "medical-mate2")
    git(w.root, "add", "radar-targets.json")
    open(os.path.join(w.adr, "0006-test.md"), "w", encoding="utf-8", newline="\n").write("---\nstatus: proposed\n---\n# t\n")
    r2 = ex.publish(["docs/adr/0006-test.md"], "스테이징에 다른 것")
    ok2 = r2["state"] == "blocked" and w.lhead() == head and w.rhead() == head and w.staged() == "radar-targets.json"
    # 정상 + 허용 밖 파일(notes.txt)은 커밋에 안 딸려 간다
    git(w.root, "reset", "-q", "--", "radar-targets.json")
    r3 = ex.publish(["docs/adr/0006-test.md"], "허용")
    files = w.commit_files(w.rhead()) if r3["state"] == "exported" else None
    return ok1 and ok2 and r3["state"] == "exported" and files == {"docs/adr/0006-test.md": "A"} and "notes.txt" not in git(w.root, "ls-files"), (r1, r2, r3, files)


@scen
def e_existing_adr_cannot_change(h, X, w):
    """기존 ADR을 고치는 커밋은 구조적으로 불가 — 경로로 넘겨도, 항목이 가리켜도, 스테이징에 끼어도"""
    ex = w.ex(X)
    head = w.lhead()
    p = adr_path(w, "0003")
    th.sub_file(p, "status: adopted", "status: rejected")  # 사람(또는 사고)이 작업 사본의 기존 ADR을 고쳐 두었다
    rel = "docs/adr/" + os.path.basename(p)
    r1 = ex.publish([rel], "기존 ADR 수정")
    ok1 = r1["state"] == "blocked" and w.rhead() == head and "rejected" not in w.remote_show(rel)
    # 항목이 기존 ADR 파일을 가리키는 경우 — 해시까지 맞춰 둔 완료 상태의 항목
    it = h.new_item(w.ctx(), "it-x", "capture", "USER_REVIEW")
    it["write_intent"] = {"adr": "0003", "path": rel, "content_hash": h._file_hash(p), "state": "done"}
    it["export"] = {"state": "pending"}
    it["scope"] = h.scope_of(D1)
    h.save_item(w.ctx(), it)
    it["fingerprint"] = baseline(w, h)  # 항목을 저장한 뒤에 — 자기 파일을 뺀 세상이 근거다
    h.save_item(w.ctx(), it)
    r2 = h.export_item(w.ctx(), "it-x", ex)
    ok2 = r2["state"] == "blocked" and w.rhead() == head and w.lhead() == head and "rejected" not in w.remote_show(rel)
    # 스테이징에 끼어 있는 기존 ADR 수정
    git(w.root, "add", rel)
    open(os.path.join(w.adr, "0006-test.md"), "w", encoding="utf-8", newline="\n").write("---\nstatus: proposed\n---\n# t\n")
    r3 = ex.publish(["docs/adr/0006-test.md"], "섞임")
    ok3 = r3["state"] == "blocked" and w.rhead() == head
    return ok1 and ok2 and ok3, (r1, r2, r3)


@scen
def e_remote_ahead_same_fingerprint(h, X, w):
    """원격이 앞섰는데(사람이 push) 세상이 같다(기록용 칸만 바뀜) → fast-forward한 뒤 재개 검사 → 같으니 커밋 · push (선형 이력)"""
    ex = w.ex(X)
    base_fp = baseline(w, h)
    w.human_push(lambda d: touch_machine(os.path.join(d, "docs", "adr"), "0004"), "revisit routine")
    r = approve(w, h, ex, baseline=base_fp)
    subj = w.subjects()
    return (r["export"]["state"] == "exported" and w.count() == 2 and subj[0] == "revisit routine" and "it-1" in subj[1]
            and git(w.remote, "rev-list", "--merges", f"{w.base}..main") == "" and w.lhead() == w.rhead()
            and git(w.remote, "rev-parse", w.rhead() + "^") == git(w.remote, "log", "--format=%H", "--grep=revisit routine", "-1")), (r.get("export"), subj)


def _status_change(d):
    th.sub_file(os.path.join(d, "docs", "adr", "0003-guardrails-pii.md"), "status: adopted", "status: rejected")


@scen
def e_remote_ahead_different_fingerprint(h, X, w):
    """원격이 앞섰고 세상이 달라졌다(0003 status 변경) → 커밋하지 않고 다시 interrupt(stale + 차이). 계속하면 그때의 세상을 근거로 커밋"""
    ex = w.ex(X)
    base_fp = baseline(w, h)
    w.human_push(_status_change, "사람이 0003을 rejected로")
    human_head = w.rhead()
    r = approve(w, h, ex, baseline=base_fp)
    e = r["export"]
    it = w.item("it-1")
    ctx = w.ctx()
    ok1 = (e["state"] == "stale" and any("0003" in l and "status: adopted → rejected" in l for l in e["changes"])
           and w.rhead() == human_head and w.lhead() == human_head and w.count() == 1
           and it["export"]["state"] == "stale" and it["attention"]["kind"] == "export_stale" and it["lifecycle"] == "USER_REVIEW"
           and "0006-kopii-small.md" in w.adrs() and "0006-kopii-small.md" in h.pending_names(ctx)
           and git(w.root, "ls-files", "--error-unmatch", "docs/adr/0006-kopii-small.md", check_rc=False) == "")
    again = h.review_item(w.ctx(), "it-1", "approve", exporter=w.ex(X))  # 답 없이 다시 — 아직 stale
    ok2 = again["state"] == "stale" and w.rhead() == human_head
    done = h.review_item(w.ctx(), "it-1", "approve", stale_answer="continue", exporter=w.ex(X))
    it2 = w.item("it-1")
    ok3 = (done["state"] == "exported" and w.count() == 2 and w.lhead() == w.rhead() and it2["export"] == {"state": "done"}
           and any(ev.get("event") == "stale_accepted" for ev in it2["history"]) and "attention" not in it2
           and "0006-kopii-small.md" not in h.pending_names(w.ctx()))
    return ok1 and ok2 and ok3, (e, again, done)


@scen
def e_stale_discard(h, X, w):
    """stale에서 버림 — 초안 파일을 지우고 항목을 닫고, 닫힌 기록만 커밋한다 (ADR은 main에 안 들어간다)"""
    ex = w.ex(X)
    base_fp = baseline(w, h)
    w.human_push(_status_change, "사람이 0003을 rejected로")
    approve(w, h, ex, baseline=base_fp)
    r = h.review_item(w.ctx(), "it-1", "approve", stale_answer="discard", exporter=w.ex(X))
    it = w.item("it-1")
    files = w.commit_files(w.rhead())
    return (r["outcome"] == "discard" and "0006-kopii-small.md" not in w.adrs() and it["lifecycle"] == "CLOSED" and it["export"]["state"] == "closed"
            and files == {".radar/items/it-1.yaml": "A"} and "0006-kopii-small.md" not in git(w.remote, "ls-tree", "-r", "--name-only", "main")), (r, files)


@scen
def e_push_race_recovers(h, X, w):
    """커밋 뒤 push가 경합으로 거부 → 로컬 커밋을 취소하고 같은 경로(동기화 → 재검사 → 커밋 → push)로 성공. 사람의 커밋은 지워지지 않는다"""
    state = {"n": 0}

    def hook(name):
        if name == "committed" and state["n"] == 0:
            state["n"] = 1
            w.human_push(lambda d: touch_machine(os.path.join(d, "docs", "adr"), "0004"), "사람이 그 사이 push")

    ex = w.ex(X, hook=hook)
    r = approve(w, h, ex)
    subj = w.subjects()
    mine = git(w.remote, "log", "--format=%H", "--grep=it-1", f"{w.base}..main").splitlines()
    return (r["export"]["state"] == "exported" and r["export"]["attempts"] == 2 and subj[0] == "사람이 그 사이 push" and len(subj) == 2
            and len(mine) == 1 and git(w.remote, "rev-parse", mine[0] + "^") == git(w.remote, "rev-parse", w.rhead() + "~1")
            and w.lhead() == w.rhead() and git(w.root, "status", "--porcelain") == ""
            and git(w.remote, "rev-list", "--merges", f"{w.base}..main") == ""), (r.get("export"), subj)


@scen
def e_push_race_then_stale(h, X, w):
    """push 경합으로 거부된 뒤 다시 보니 세상이 달라졌다 → 커밋은 취소돼 있고(스테이징·로컬 커밋 없음), 파일은 write-ahead 그대로, 사람의 push가 main에 남는다"""
    state = {"n": 0}

    def hook(name):
        if name == "committed" and state["n"] == 0:
            state["n"] = 1
            w.human_push(_status_change, "사람이 그 사이 0003을 바꿈")

    r = approve(w, h, w.ex(X, hook=hook))
    human_head = w.rhead()
    it = w.item("it-1")
    ok1 = (r["export"]["state"] == "stale" and w.count() == 1 and w.lhead() == human_head
           and git(w.root, "rev-list", "--count", "origin/main..HEAD") == "0" and w.staged() == ""
           and it["export"]["state"] == "stale" and it["write_intent"]["state"] == "done"
           and h._file_hash(os.path.join(w.adr, "0006-kopii-small.md")) == it["write_intent"]["content_hash"])
    done = h.review_item(w.ctx(), "it-1", "approve", stale_answer="continue", exporter=w.ex(X))
    return ok1 and done["state"] == "exported" and w.count() == 2 and w.lhead() == w.rhead(), (r.get("export"), done)


@scen
def e_sync_blocks(h, X, w):
    """동기화가 막히는 경우 — 로컬에 안 민 커밋 · 다른 브랜치 · 같은 번호가 그 사이 들어옴. 어느 쪽도 커밋하지 않는다"""
    ex = w.ex(X)
    head = w.lhead()
    base_fp = baseline(w, h)
    # 같은 번호(0006)가 원격에 들어왔다
    w.human_push(lambda d: open(os.path.join(d, "docs", "adr", "0006-other.md"), "w", encoding="utf-8", newline="\n").write(
        "---\nstatus: adopted\ntechnology: Other\nuse: x\naffected: []\nevidence: []\n---\n# 사람이 만든 것\n"), "사람이 0006을 만듦")
    r1 = approve(w, h, ex, baseline=base_fp)
    ok1 = r1["export"]["state"] == "blocked" and r1["export"]["reason"] == "number_taken" and w.count() == 1 and w.item("it-1")["export"]["state"] == "blocked"
    # 로컬에 안 민 커밋
    w2 = GWorld()
    git(w2.root, "commit", "-q", "--allow-empty", "-m", "안 민 커밋")
    r2 = approve(w2, h, w2.ex(X))
    ok2 = r2["export"]["state"] == "blocked" and r2["export"]["reason"] == "local_ahead_or_diverged" and w2.count() == 0
    w2.cleanup()
    # 다른 브랜치
    w3 = GWorld()
    git(w3.root, "checkout", "-q", "-b", "other")
    r3 = approve(w3, h, w3.ex(X))
    ok3 = r3["export"]["state"] == "blocked" and r3["export"]["reason"] == "wrong_branch" and w3.count() == 0
    w3.cleanup()
    return ok1 and ok2 and ok3, (r1.get("export"), r2.get("export"), r3.get("export"))


@scen
def e_detect_review(h, X, w):
    """detect — 초안은 로컬에 쓰고 내보내지 않는다(검토 대기). 지문은 로컬 초안을 세상으로 안 본다. approve는 내보내기, reject는 초안을 지우고 닫는다"""
    ex = w.ex(X)
    before_fp = baseline(w, h)
    r1 = approve(w, h, ex, item="d-1", hold=True, kind="detect")
    r2 = approve(w, h, ex, draft=draft2(), item="d-2", hold=True, kind="detect")
    ctx = w.ctx()
    its = [w.item("d-1"), w.item("d-2")]
    q = [x["item"] for x in h.review_queue(ctx)]
    same_after, _ = verdict(h, w, before_fp)
    ok1 = (r1["export"]["state"] == "review" and r2["export"]["state"] == "review" and w.count() == 0 and w.lhead() == w.base
           and all(i["lifecycle"] == "USER_REVIEW" and i["export"]["state"] == "review" for i in its) and q == ["d-1", "d-2"]
           and len(w.adrs()) == 7 and same_after and git(w.root, "log", "--oneline", f"{w.base}..HEAD") == "")
    ra = h.review_item(w.ctx(), "d-1", "approve", exporter=w.ex(X))
    ok2 = ra["state"] == "exported" and w.count() == 1 and w.item("d-1")["export"]["state"] == "done" and w.item("d-2")["export"]["state"] == "review"
    rr = h.review_item(w.ctx(), "d-2", "reject", exporter=w.ex(X))
    files = w.commit_files(w.rhead())
    i2 = w.item("d-2")
    ok3 = (rr["outcome"] == "reject" and rr["state"] == "exported" and i2["lifecycle"] == "CLOSED" and i2["export"]["state"] == "closed"
           and files == {".radar/items/d-2.yaml": "A"} and w.count() == 2 and len(w.adrs()) == 6 and h.pending_names(w.ctx()) == set()
           and "d-2" in git(w.remote, "log", "-1", "--format=%s", w.rhead()))
    try:  # 이미 내보낸 항목은 검토 대기가 아니다 — reject로도 지울 수 없다
        h.review_item(w.ctx(), "d-1", "reject", exporter=w.ex(X))
        ok4 = False
    except h.HitlError:
        ok4 = os.path.exists(os.path.join(w.adr, "0006-kopii-small.md"))
    return ok1 and ok2 and ok3 and ok4, (r1.get("export"), ra, rr)


@scen
def e_detect_review_needs_exporter(h, X, w):
    """내보내기가 꺼져 있으면 검토 답을 반영하지 않는다 (조용히 삭제·커밋하지 않는다)"""
    r = approve(w, h, w.ex(X), item="d-1", hold=True, kind="detect")
    w.dry = True
    try:
        h.review_item(w.ctx(), "d-1", "reject")
        raised = False
    except h.HitlError:
        raised = True
    return r["export"]["state"] == "review" and raised and "0006-kopii-small.md" in w.adrs() and w.item("d-1")["export"]["state"] == "review", r


# 같은 실행에서 제품이 직접 내보낸 변화는 지문 비교에서 넘긴다 (설계 §5-2 「구현에서 정한 것」 ①) ──────────

def draft3():
    d = th.draft2()
    d.update(title="BarOCR로 영수증을 읽을지", technology="BarOCR", use="영수증 OCR")
    return AdrDraft.model_validate(d)


def draft_same_tech():
    """D1과 같은 기술·component, 다른 use — B′의 match(overlap)에 앞선 초안의 ADR이 걸린다"""
    d = json.loads(json.dumps(th.KOPII["draft"]))
    d.update(title="KoPII-small을 다른 용도로도 쓸지", use="요약 전 마스킹")
    return AdrDraft.model_validate(d)


def _detect_batch(w, h, ex, runs, drafts=None):
    """detect 초안 여러 개를 쓰고(검토 대기) 돌려준다 — runs[i]가 i번째 초안의 run_id"""
    drafts = drafts or [D1, draft2(), draft3()]
    for i, (r, d) in enumerate(zip(runs, drafts), 1):
        approve(w, h, ex, draft=d, item=f"d-{i}", hold=True, kind="detect", run_id=r)


def _approve_all(w, h, X, n):
    return [h.review_item(w.ctx(), f"d-{i}", "approve", exporter=w.ex(X)) for i in range(1, n + 1)]


@scen
def e_same_run_chain_no_stale(h, X, w):
    """같은 실행 detect 초안 3개를 연달아 승인 → stale 없이 전부 내보내진다 (A′). 넘긴 것은 별도 파일 history에 남는다"""
    ex = w.ex(X)
    _detect_batch(w, h, ex, ["run-A"] * 3)
    rs = _approve_all(w, h, X, 3)
    its = [w.item(f"d-{i}") for i in (1, 2, 3)]
    skipped = [[e for e in it["history"] if e.get("event") == "own_exports_skipped"] for it in its]
    ok = (all(r["state"] == "exported" for r in rs) and w.count() == 3 and w.lhead() == w.rhead()
          and all(it["export"] == {"state": "done"} and it["run_id"] == "run-A" for it in its)
          and not skipped[0] and skipped[1][0]["adrs"] == ["0006-kopii-small.md"]
          and skipped[2][0]["adrs"] == ["0006-kopii-small.md", "0007-fooner.md"])
    return ok, rs


@scen
def e_same_run_chain_related_fingerprint(h, X, w):
    """B′(RADAR_FINGERPRINT=related)도 같다 — 앞선 초안과 같은 기술이라 match 결과에 그 ADR이 새로 걸려도 넘긴다"""
    w.extra_env["RADAR_FINGERPRINT"] = "related"
    ex = w.ex(X)
    _detect_batch(w, h, ex, ["run-A"] * 2, drafts=[D1, draft_same_tech()])
    fp = w.item("d-2")["fingerprint"]
    rs = _approve_all(w, h, X, 2)
    ok = fp["impl"] == "related" and all(r["state"] == "exported" for r in rs) and w.count() == 2
    return ok, rs


@scen
def e_same_run_but_human_edited(h, X, w):
    """같은 실행이지만 그 사이 사람이 첫 초안의 ADR을 고쳤다(내보낸 해시와 다르다) → 둘째는 stale — 바깥 변화다"""
    ex = w.ex(X)
    _detect_batch(w, h, ex, ["run-A"] * 2)
    r1 = h.review_item(w.ctx(), "d-1", "approve", exporter=w.ex(X))
    w.human_push(lambda d: th.sub_file(os.path.join(d, "docs", "adr", "0006-kopii-small.md"), "status: proposed", "status: adopted"), "사람이 0006을 adopted로")
    r2 = h.review_item(w.ctx(), "d-2", "approve", exporter=w.ex(X))
    ok = (r1["state"] == "exported" and r2["state"] == "stale" and any("0006" in l for l in r2["changes"])
          and w.count() == 2 and w.item("d-2")["export"]["state"] == "stale")
    return ok, (r1, r2)


@scen
def e_other_run_is_outside_change(h, X, w):
    """다른 실행이 내보낸 ADR은 바깥 변화다 — run_id가 다르거나 둘 중 하나라도 없으면 stale (다른 실행 = 다른 판단 맥락)"""
    ex = w.ex(X)
    _detect_batch(w, h, ex, ["run-A", "run-B"], drafts=[D1, draft2()])
    r1 = h.review_item(w.ctx(), "d-1", "approve", exporter=w.ex(X))
    r2 = h.review_item(w.ctx(), "d-2", "approve", exporter=w.ex(X))
    ok1 = r1["state"] == "exported" and r2["state"] == "stale" and any("새 ADR 0006" in l for l in r2["changes"]) and w.count() == 1
    w2 = GWorld()  # run_id가 없는 항목(capture·옛 항목)끼리는 같은 실행이 아니다 — None == None으로 넘기지 않는다
    _detect_batch(w2, h, w2.ex(X), [None, None], drafts=[D1, draft2()])
    q1 = h.review_item(w2.ctx(), "d-1", "approve", exporter=w2.ex(X))
    q2 = h.review_item(w2.ctx(), "d-2", "approve", exporter=w2.ex(X))
    ok2 = q1["state"] == "exported" and q2["state"] == "stale" and w2.count() == 1
    w2.cleanup()
    return ok1 and ok2, (r2.get("changes"), q2.get("changes"))


@scen
def e_same_run_plus_outside_change(h, X, w):
    """같은 실행의 내보내기에 바깥 변화(사람이 0003 status 변경)가 섞이면 stale — 차이 요약에는 바깥 변화만 있다 (A′·B′)"""
    out = []
    for name in ("all", "related"):
        ww = GWorld(extra_env={"RADAR_FINGERPRINT": name})
        _detect_batch(ww, h, ww.ex(X), ["run-A"] * 2, drafts=[D1, draft_same_tech()])
        r1 = h.review_item(ww.ctx(), "d-1", "approve", exporter=ww.ex(X))
        ww.human_push(_status_change, "사람이 0003을 rejected로")
        r2 = h.review_item(ww.ctx(), "d-2", "approve", exporter=ww.ex(X))
        ch = r2.get("changes") or []
        out.append((name, r1["state"] == "exported" and r2["state"] == "stale" and len(ch) == 1 and "0003" in ch[0]
                    and "0006" not in " ".join(ch) and ww.count() == 2, ch))
        ww.cleanup()
    return all(ok for _, ok, _ in out), out


class CountingProc:
    """subprocess 대역 — git 호출 수를 센다 (실제 실행은 넘긴다)."""

    def __init__(self):
        self.git_calls = []

    def run(self, argv, *a, **k):
        if argv and os.path.basename(str(argv[0])).lower().startswith("git"):
            self.git_calls.append(list(argv))
        return subprocess.run(argv, *a, **k)

    def __getattr__(self, name):
        return getattr(subprocess, name)


@scen
def e_dry_run_never_exports(h, X, w):
    """eval·dry-run·스텁에서는 절대 내보내지 않는다 — exporter_for가 no-op을 주고, GitExporter를 일부러 만들어도 git을 안 부른다"""
    saved = X.subprocess
    X.subprocess = cp = CountingProc()
    try:
        # RADAR_EXPORT=git이어도 dry-run이면 no-op (eval 러너의 환경과 같다)
        w.dry = True
        ctx = w.ctx()
        a = h.exporter_for(ctx)
        # RADAR_EXPORT이 없으면 dry-run이 아니어도 no-op
        w.dry = False
        w.extra_env["RADAR_EXPORT"] = ""
        b = h.exporter_for(w.ctx())
        w.extra_env.pop("RADAR_EXPORT")
        c = h.exporter_for(w.ctx())  # 둘 다 켰을 때만 GitExporter
        # 일부러 dry_run인 GitExporter를 만들어도
        ex = X.GitExporter(w.root, dry_run=True, identity=IDENT)
        open(os.path.join(w.adr, "0006-test.md"), "w", encoding="utf-8", newline="\n").write("---\nstatus: proposed\n---\n# t\n")
        pub, syn = ex.publish(["docs/adr/0006-test.md"], "m"), ex.sync()
        try:
            ex._git("status")
            direct = False
        except X.ExportRefused:
            direct = True
        return (not a.active and not b.active and c.active and pub["state"] == "skipped" and syn["state"] == "skipped" and direct
                and cp.git_calls == [] and w.count() == 0), (a, b, c, pub, syn, cp.git_calls)
    finally:
        X.subprocess = saved


@scen
def e_dry_run_graph_no_git(h, X, w):
    """dry-run(= eval) 환경에서 RADAR_EXPORT=git이 켜져 있어도 write_adr 전체가 git을 안 부르고, 항목에 export 칸도 안 생긴다"""
    saved = X.subprocess
    X.subprocess = cp = CountingProc()
    try:
        w.dry = True
        ctx = w.ctx()
        r = h.write_adr(ctx, "it-1", "capture", D1)
        r2 = h.write_adr(w.ctx(), "d-1", "detect", draft2(), lifecycle="REVIEW_CANDIDATE", final_lifecycle="REVIEW_CANDIDATE", by="graph", hold_export=True)
        it, d1 = w.item("it-1"), w.item("d-1")
        return (cp.git_calls == [] and "export" not in r and "export" not in it and "export" not in d1 and it["lifecycle"] == "DONE"
                and d1["lifecycle"] == "REVIEW_CANDIDATE" and w.count() == 0 and w.lhead() == w.base), (cp.git_calls, r, r2)
    finally:
        X.subprocess = saved


@scen
def e_eval_runner_is_dry_run(h, X, w):
    """전제 확인 — eval 어댑터·러너가 RADAR_DRY_RUN=1을 세운다 (이 전제가 깨지면 dry-run 잠금이 eval을 못 지킨다)"""
    paths = [os.path.join(ROOT, "product", "spike", "common", "eval_adapter.py"), os.path.join(ROOT, "eval", "runner", "capture.py"),
             os.path.join(ROOT, "eval", "runner", "run_case.py")]
    miss = [p for p in paths if not re.search(r'"RADAR_DRY_RUN":\s*"1"', open(p, encoding="utf-8").read())]
    return not miss, miss


@scen
def e_review_cli(h, X, w):
    """review.py — 새 프로세스로 검토 답을 넣는다: --list · approve(커밋) · reject · dry-run이면 거부"""
    ex = w.ex(X)
    approve(w, h, ex, item="d-1", hold=True, kind="detect")
    approve(w, h, ex, draft=draft2(), item="d-2", hold=True, kind="detect")

    def cli(*args, env=None):
        p = subprocess.run([sys.executable, os.path.join(HERE, "review.py"), "--radar", SKILL_RADAR, *args], env=env or w.env(),
                           capture_output=True, text=True, encoding="utf-8")
        return p.returncode, json.loads(p.stdout)

    rc0, lst = cli("--list")
    dry_env = {**w.env(), "RADAR_DRY_RUN": "1"}
    rc1, dry = cli("--item", "d-1", "--decision", "approve", env=dry_env)
    dry_untouched = w.count() == 0 and "0006-kopii-small.md" in w.adrs() and w.item("d-1")["export"]["state"] == "review"
    rc2, ap = cli("--item", "d-1", "--decision", "approve")
    rc3, rj = cli("--item", "d-2", "--decision", "reject")
    rc4, bad = cli("--item", "d-1", "--decision", "reject")
    ok_list = rc0 == 0 and [x["item"] for x in lst["queue"]] == ["d-1", "d-2"]
    ok_dry = rc1 == 2 and "error" in dry and dry_untouched
    ok_ans = rc2 == 0 and ap["state"] == "exported" and rc3 == 0 and rj["outcome"] == "reject" and w.count() == 2 and rc4 == 2 and "error" in bad
    return ok_list and ok_dry and ok_ans, (lst, dry, ap, rj, bad)


# 2. 그래프 ───────────────────────────────────────────────────────────

def graph_scen():
    print("2. 그래프 — capture 2턴에서 A′ · 스위치 · 내보내기")

    # 기본 A′: revisit routine이 쓰는 칸은 다시 묻지 않는다 ((A)는 묻는다)
    for impl, expect_stale in (("all", False), ("repo_hash", True)):
        w = GWorld(dry=True)
        rep, _ = th.turn1(w, KOPII, fingerprint=impl)
        iid = rep["item_id"]
        touch_machine(w.adr, "0004")
        rep2, _ = th.turn2(w, KOPII, "approve", item_id=iid)
        stale = bool((rep2.get("hitl") or {}).get("stale"))
        check(f"[{impl}] 대기 중 revisit routine이 revisit_last_checked를 갱신 → {'다시 묻는다(기각된 (A)의 헛경보)' if expect_stale else '다시 묻지 않고 승인'}",
              stale == expect_stale and (stale or w.item(iid)["write_intent"]["state"] == "done"), rep2.get("hitl"))
        w.cleanup()

    w = GWorld(dry=True)
    rep, _ = th.turn1(w, KOPII)
    check("기본 지문은 A′(all)", hitl.StateStore(w.state).get(rep["item_id"])[0]["impl"] == "all")
    th.sub_file(adr_path(w, "0004"), "status: hold", "status: rejected")
    rep2, _ = th.turn2(w, KOPII, "approve", item_id=rep["item_id"])
    st = (rep2.get("hitl") or {}).get("stale")
    check("status 변화(0004 hold → rejected) → 다시 interrupt + 요약", st and rep2["awaiting_approval"] and any("0004" in c and "hold → rejected" in c for c in st["changes"]) and not w.items(), rep2.get("hitl"))
    w.cleanup()

    w = GWorld(dry=True)
    rep, _ = th.turn1(w, KOPII)
    th.sub_file(w.target_md("medical-mate"), "| boto3 | pii-guard |", "| boto3 | pii-guard |\n| httpx | chat-api |")
    rep2, _ = th.turn2(w, KOPII, "approve", item_id=rep["item_id"])
    st = (rep2.get("hitl") or {}).get("stale")
    check("대상 repo 스택 절 변화 → 다시 interrupt + 어느 repo가 바뀌었는지", st and any("medical-mate" in c and "httpx" in c for c in st["changes"]) and not w.items(), rep2.get("hitl"))
    w.cleanup()

    w = GWorld(dry=True, extra_env={"RADAR_FINGERPRINT": "related"})
    rep, _ = th.turn1(w, KOPII)
    iid = rep["item_id"]
    check("스위치 RADAR_FINGERPRINT=related → B′ 지문이 저장된다", hitl.StateStore(w.state).get(iid)[0]["impl"] == "related")
    open(adr_path(w, "0005"), "a", encoding="utf-8", newline="\n").write("\n무관한 변화\n")
    rep2, _ = th.turn2(w, KOPII, "approve", item_id=iid)
    check("  B′는 무관한 ADR 변화를 다시 묻지 않는다", not (rep2.get("hitl") or {}).get("stale") and w.item(iid)["write_intent"]["state"] == "done", rep2.get("hitl"))
    w.cleanup()

    # 내보내기 — 켜진 세계(dry 아님 + RADAR_EXPORT=git)
    w = GWorld()
    rep, _ = th.turn1(w, KOPII)
    iid = rep["item_id"]
    check("[git] 승인 전에는 ADR repo 쓰기 0, 커밋 0 (R9)", w.count() == 0 and not os.path.exists(os.path.join(w.root, ".radar")) and git(w.root, "status", "--porcelain") == "")
    w.human_push(lambda d: touch_machine(os.path.join(d, "docs", "adr"), "0004"), "revisit routine")
    rep2, _ = th.turn2(w, KOPII, "approve", item_id=iid)
    files = w.commit_files(w.rhead())
    check("[git] 원격이 앞섰어도(기록용 칸만) 따라잡고 승인 → 커밋 1개 push · checkpoint 정리 · 보고서에 export 상태",
          not rep2["awaiting_approval"] and w.count() == 2 and files.get("docs/adr/0006-kopii-small.md") == "A" and w.lhead() == w.rhead()
          and rep2["hitl"]["export"]["state"] == "done" and not th.thread_exists(w, iid), (rep2.get("hitl"), files))
    w.cleanup()

    w = GWorld()
    rep, _ = th.turn1(w, KOPII)
    iid = rep["item_id"]
    w.human_push(_status_change, "사람이 0003을 rejected로")
    rep2, _ = th.turn2(w, KOPII, "approve", item_id=iid)
    st = (rep2.get("hitl") or {}).get("stale")
    check("[git] 원격에 사람의 status 변경 → 재개 전 따라잡고 지문 비교 → 쓰지 않고 다시 interrupt", st and rep2["awaiting_approval"] and any("0003" in c for c in st["changes"])
          and w.count() == 1 and not w.items() and th.pending(w, KOPII, iid) == 1, rep2.get("hitl"))
    rep3, _ = th.turn2(w, KOPII, "approve", item_id=iid, stale_answer="continue")
    check("[git] 계속 → 그 세상을 근거로 승인 · 커밋 1개", w.count() == 2 and rep3["hitl"]["export"]["state"] == "done" and w.lhead() == w.rhead(), rep3.get("hitl"))
    w.cleanup()

    w = GWorld(dry=True, extra_env={"RADAR_EXPORT": "git"})
    saved = export_mod.subprocess
    export_mod.subprocess = cp = CountingProc()
    try:
        rep, _ = th.turn1(w, KOPII)
        rep2, _ = th.turn2(w, KOPII, "approve", item_id=rep["item_id"])
    finally:
        export_mod.subprocess = saved
    it = w.item(rep["item_id"])
    check("[dry-run + RADAR_EXPORT=git] capture 2턴 전체에서 git 호출 0 · export 칸 없음 · ADR은 로컬에 쓰였다", cp.git_calls == [] and "export" not in it
          and it["write_intent"]["state"] == "done" and "export" not in (rep2.get("hitl") or {}) and w.count() == 0, (cp.git_calls, rep2.get("hitl")))
    w.cleanup()


# ── 변이 ─────────────────────────────────────────────────────────────

SRC_H = open(os.path.join(HERE, "hitl.py"), encoding="utf-8").read()
SRC_X = open(os.path.join(HERE, "export.py"), encoding="utf-8").read()


def load_src(src, patches, modname):
    for old, new in patches:
        assert src.count(old) == 1, f"변이가 소스에 안 맞는다(헛돌 수 있다): {old[:70]!r} × {src.count(old)}"
        src = src.replace(old, new)
    tmp = os.path.join(tempfile.mkdtemp(prefix="jhs-mut-x-"), modname.split(".")[-1] + ".py")
    open(tmp, "w", encoding="utf-8", newline="\n").write(src)
    spec = importlib.util.spec_from_file_location(modname, tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


X_GUARD = '''if a in ("-f", "--force", "--force-with-lease", "--mirror", "--delete") or a.startswith("+") or a.startswith("--force"):'''
MUTANTS = [
    ("기록용 칸 포함 (revisit_last_checked도 해시)", "hitl", [("if k not in MACHINE_KEYS_ANYWHERE and not (top and k in MACHINE_FIELDS)}  # MUT:machine", "if True}  # MUT:machine")],
     ["f_machine_fields_only", "f_condition_body_and_nested_machine_key"]),
    ("스택 절 무시", "hitl", [('and old.get("stack") == new.get("stack"))  # MUT:stack', "and True)  # MUT:stack")], ["f_stack_sections"]),
    ("허용 목록 무시 (경로)", "export", [("if not path_allowed(rel):  # MUT:allowlist", "if False:  # MUT:allowlist")], ["e_allowlist"]),
    ("스테이징 검사 생략 (섞여 커밋)", "export", [("if self._staged():  # MUT:prestaged", "if False:  # MUT:prestaged"), ("if extra:  # MUT:staged_scope", "if False:  # MUT:staged_scope")],
     ["e_allowlist", "e_existing_adr_cannot_change"]),
    ("force push 허용", "export", [(X_GUARD, "if False:"), ('p = self._git("push", self.remote, f"HEAD:{self.branch}", ok=None)', 'p = self._git("push", "--force", self.remote, f"HEAD:{self.branch}", ok=None)')],
     ["e_push_race_recovers", "e_push_race_then_stale"]),
    ("원격 앞섬인데 재검사 생략 (publish가 check를 안 부름)", "export", [("if check is not None:  # MUT:recheck", "if False:  # MUT:recheck")],
     ["e_remote_ahead_different_fingerprint", "e_push_race_then_stale"]),
    ("원격 앞섬인데 재검사 생략 (지문이 늘 같다고 함)", "hitl", [("if old is not None and fp.same(old, cmp_new):  # MUT:recheck_same", "if True:  # MUT:recheck_same")],
     ["e_remote_ahead_different_fingerprint", "e_push_race_then_stale"]),
    ("eval·dry-run에서 내보냄 (exporter_for가 dry-run을 안 본다)", "hitl", [('and not ctx.dry_run:  # MUT:exporter_for', ':  # MUT:exporter_for'), ("GitExporter(repo_root(ctx), dry_run=ctx.dry_run,", "GitExporter(repo_root(ctx), dry_run=False,")],
     ["e_dry_run_never_exports", "e_dry_run_graph_no_git"]),
    ("eval·dry-run에서 내보냄 (GitExporter가 dry-run에서도 git을 부름)", "export",
     [("if self.dry_run:  # MUT:dry_run_git", "if False:  # MUT:dry_run_git"), ("if self.dry_run:  # MUT:dry_run_sync", "if False:  # MUT:dry_run_sync"), ("if self.dry_run:  # MUT:dry_run_publish", "if False:  # MUT:dry_run_publish")],
     ["e_dry_run_never_exports"]),
    ("기존 ADR 수정 허용", "export", [("if ALLOW_ADR.match(rel) and self.is_tracked(rel):  # MUT:existing_adr", "if False:  # MUT:existing_adr"), ('if ALLOW_ADR.match(rel) and st != "A":  # MUT:adr_added_only', "if False:  # MUT:adr_added_only")],
     ["e_existing_adr_cannot_change"]),
    ("push 거부돼도 로컬 커밋을 안 되돌림", "export", [('if self._out("rev-parse", "HEAD") == sha:  # MUT:undo', "if False:  # MUT:undo")], ["e_push_race_recovers", "e_push_race_then_stale"]),
    ("같은 실행 판별에서 run_id 무시 (남이 내보낸 새 ADR이면 다 넘김)", "hitl", [('if not isinstance(wi, dict) or o.get("run_id") != rid:  # MUT:own_run', "if not isinstance(wi, dict):  # MUT:own_run")],
     ["e_other_run_is_outside_change"]),
    ("run_id가 없어도 같은 실행으로 봄 (None == None)", "hitl", [("if not rid or old is None:", "if old is None:")], ["e_other_run_is_outside_change"]),
    ("내보낸 내용 해시 확인 생략 (사람이 고쳐도 넘김)", "hitl", [('and path and os.path.exists(path) and _file_hash(path) == wi.get("content_hash")):  # MUT:own_hash', "and True):  # MUT:own_hash")],
     ["e_same_run_but_human_edited"]),
    ("B′: match 결과에서 앞선 내보내기의 번호를 안 뺌", "hitl", [('out["match"][k] = [i for i in ids if i != n[:4]]', "pass")], ["e_same_run_chain_related_fingerprint"]),
    ("B′: match가 아직 안 내보낸 로컬 초안을 세상으로 봄", "hitl", [('return {"exact": sorted(exact - own), "overlap": sorted(overlap - own), "related": sorted(related - own)}', 'return {"exact": sorted(exact), "overlap": sorted(overlap), "related": sorted(related)}')],
     ["e_same_run_chain_related_fingerprint"]),
    ("같은 실행 넘기기 전체 끔 (회귀 — 기능 없음)", "hitl", [("own = sorted(_own_exports(ctx, item, old, new))", "own = []")], ["e_same_run_chain_no_stale"]),
    ("넘길 때 바깥 변화까지 같이 삼킴 (own이 있으면 늘 같다고 함)", "hitl", [("if old is not None and fp.same(old, cmp_new):  # MUT:recheck_same", "if old is not None and (fp.same(old, cmp_new) or own):  # MUT:recheck_same")],
     ["e_same_run_plus_outside_change"]),
    ("같은 번호가 들어와도 커밋", "hitl", [("if taken:  # 그 사이 같은 번호의 다른 ADR이", "if False:  # 그 사이 같은 번호의 다른 ADR이")], ["e_sync_blocks"]),
]


def run_scen(names, h, X):
    out = []
    for n in names:
        w = GWorld()
        try:
            ok, detail = SCEN[n](h, X, w)
        except Exception as e:  # noqa: BLE001 — 변이본이 예외로 깨져도 「잡힌 것」이다
            ok, detail = False, f"{type(e).__name__}: {e}"
        finally:
            w.cleanup()
        out.append((n, bool(ok), detail))
    return out


def main():
    print("1. 지문 A′ · 스위치 B′ / 3. 내보내기 (함수 단위)")
    for n, ok, detail in run_scen(list(SCEN), hitl, export_mod):
        check(n, ok, detail)

    graph_scen()

    print("4. 변이 — 일부러 틀리게 고친 hitl.py·export.py를 위 검사가 잡는다 (규율 3)")
    for i, (label, target, patches, names) in enumerate(MUTANTS):
        if target == "hitl":
            mod = load_src(SRC_H, patches, f"radar.hitl_mutx_{i}")
            res = run_scen(names, mod, export_mod)
        else:
            mod = load_src(SRC_X, patches, f"radar.export_mutx_{i}")
            res = run_scen(names, hitl, mod)
        caught = [n for n, ok, _ in res if not ok]
        check(f"변이 「{label}」 → 잡힘", bool(caught), "잡히지 않았다 — 검사가 무력하다")
        if caught:
            print(f"       잡은 검사: {caught}")

    for d in _TPL:
        shutil.rmtree(d, ignore_errors=True)
    print(f"\n{'FAILED ' + str(len(fails)) + ': ' + ', '.join(fails) if fails else 'all passed'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

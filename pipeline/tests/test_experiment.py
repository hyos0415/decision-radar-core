"""실험 실행기(pipeline/experiment.py, HYO-116) 오프라인 테스트 — 네트워크 · uv · 모델 없이.

  python pipeline/tests/test_experiment.py

가짜 ADR repo(임시 폴더: docs/adr · radar-targets.json · radar-checks.json)로 본다:
- 레시피 고르기: 의존성 릴리스 대응이면 dependency · 아니면 「레시피 없음」 · 버전 못 읽음 · 검사 설정 없음 · 돌릴 곳 없음은 이유로
- 돌리기: 올려 보기 결과를 공통 모양으로 · experiment.json을 남긴다 · 돌릴 수 없으면 레시피를 부르지 않는다
- 비용 문: local이 아닌 레시피는 confirm 없이 안 돈다
- 스텁: 레시피 고르기는 진짜, 올려 보기는 부르지 않는다
뒤쪽 변이 검사(규율 3): 비용 문을 빼거나 · 「레시피 없음」인데도 돌리면 위 테스트가 빨개져야 한다.
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import experiment, tryout  # noqa: E402

DEP = "---\nstatus: proposed\ntechnology: zz-web\nuse: 의존성 릴리스 2.0.0 대응 (breaking)\naffected:\n  - {repo: app, component: api}\n---\n# zz-web 2.0.0\n"
NOVER = "---\nstatus: proposed\ntechnology: zz-web\nuse: 의존성 릴리스 보안 패치 대응 (security)\naffected:\n  - {repo: app, component: api}\n---\n# zz-web\n"
ELSEWHERE = "---\nstatus: proposed\ntechnology: zz-web\nuse: 의존성 릴리스 2.0.0 대응 (breaking)\naffected:\n  - {repo: gone, component: x}\n---\n# zz-web\n"
MODEL = "---\nstatus: proposed\ntechnology: Gemini 4 Carbon\nuse: 신규 모델 평가 후보 검토\naffected:\n  - {repo: app, component: eval}\n---\n# Gemini\n"
CHECKS = {"app": {"python": "3.12", "components": {"api": {"manifests": ["requirements.txt"], "run": [{"script": "t.py"}]}}}}


def adr_repo(d, checks=True, files=None, reqs="zz-web==1.0.0\n"):
    root = Path(d) / "adr"
    (root / "docs" / "adr").mkdir(parents=True)
    (Path(d) / "app").mkdir()
    (Path(d) / "app" / "requirements.txt").write_text(reqs, encoding="utf-8")      # 대상 repo 작업 사본의 manifest — plan이 고정 줄을 미리 본다
    (root / "radar-targets.json").write_text(json.dumps([{"repo": "app", "path": "../app"}]), encoding="utf-8")
    if checks:
        (root / "radar-checks.json").write_text(json.dumps(CHECKS), encoding="utf-8")
    paths = {}
    for name, text in (files or {"0001-dep.md": DEP}).items():
        p = root / "docs" / "adr" / name
        p.write_text(text, encoding="utf-8")
        paths[name] = p
    return root, paths


def test_plan_picks_dependency_and_where():
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d)
        pl = experiment.plan(p["0001-dep.md"])
        assert pl["recipe"] == "dependency" and pl["ready"] and pl["cost"] == "local", pl
        assert pl["params"] == {"package": "zz-web", "to": "2.0.0", "repo": "app", "component": "api"}, pl
        assert "zz-web 1.0.0 → 2.0.0 (app · api)" in pl["detail"] and pl["export_rule"].startswith("통과"), pl


def test_plan_checks_the_pin_exists_now():
    """이미 걷어낸 의존성(langchain · ADR 0030)이 「돌릴 수 있음」으로 보이지 않게 — 대상 repo manifest의 고정 줄을 미리 본다 (HYO-116)."""
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, reqs="other==1.0\n")
        gone = experiment.plan(p["0001-dep.md"])
    assert not gone["ready"] and "고정 줄" in gone["reason"] and "requirements.txt" in gone["reason"], gone
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, reqs="zz-web==2.0.0\n")
        done = experiment.plan(p["0001-dep.md"])
    assert not done["ready"] and "이미 2.0.0" in done["reason"], done


def _npm_app(d, locked="1.0.0", run=True):
    """대상 repo에 web/package.json · lockfile을 두고 검사 설정에 npm 칸을 더한다 (HYO-248)."""
    web = Path(d) / "app" / "web"
    web.mkdir()
    (web / "package.json").write_text(tryout._json_text({"name": "web", "devDependencies": {"zz-web": "^1.0.0"}}), encoding="utf-8")
    (web / "package-lock.json").write_text(tryout._json_text({"name": "web", "lockfileVersion": 3, "packages": {
        "": {"devDependencies": {"zz-web": "^1.0.0"}}, "node_modules/zz-web": {"version": locked, "resolved": "r", "integrity": "i"}}}), encoding="utf-8")
    comp = {**CHECKS["app"]["components"]["api"], "npm": {"dir": "web", "run": [{"cmd": ["npm", "run", "build"]}] if run else []}}
    (Path(d) / "adr" / "radar-checks.json").write_text(json.dumps({"app": {"python": "3.12", "components": {"api": comp}}}), encoding="utf-8")


def test_plan_reads_npm_version():
    """package.json에 있으면 npm 경로 — Python 고정 줄이 없어도 lockfile의 설치 버전을 미리 본다 (HYO-248)."""
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, reqs="other==1.0\n")
        _npm_app(d)
        pl = experiment.plan(p["0001-dep.md"])
    assert pl["ready"] and "zz-web 1.0.0 → 2.0.0 (app · api)" in pl["detail"] and "package.json · lockfile 올림" in pl["detail"], pl
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, reqs="other==1.0\n")
        _npm_app(d, locked="2.0.0")
        done = experiment.plan(p["0001-dep.md"])
    assert not done["ready"] and "이미 2.0.0" in done["reason"] and "package-lock.json" in done["reason"], done
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, reqs="other==1.0\n")
        _npm_app(d, run=False)
        none = experiment.plan(p["0001-dep.md"])
    assert not none["ready"] and "npm 검사" in none["reason"], none


def test_plan_reasons_instead_of_guessing():
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, files={"0001-nover.md": NOVER, "0002-else.md": ELSEWHERE, "0003-model.md": MODEL, "0004-bad.md": "no front matter\n"})
        nover, other, model, bad = (experiment.plan(p[k]) for k in ("0001-nover.md", "0002-else.md", "0003-model.md", "0004-bad.md"))
    assert not nover["ready"] and "버전" in nover["reason"], nover
    assert not other["ready"] and "돌릴 곳이 없다" in other["reason"], other
    assert model["recipe"] is None and not model["ready"] and model["reason"] == experiment.NO_RECIPE, model
    assert bad["recipe"] is None and not bad["ready"] and "못 읽었다" in bad["reason"], bad
    with tempfile.TemporaryDirectory() as d:
        _, p = adr_repo(d, checks=False)
        pl = experiment.plan(p["0001-dep.md"])
    assert not pl["ready"] and "radar-checks.json" in pl["reason"], pl


def fake_tryout(calls, verdict="pass", export=None):
    def run(adr, repo_name, repo_path, component, check, package, version, out, env=None, keep=False, extra=(), export_flag=None, **kw):
        calls.append({"repo": repo_name, "component": component, "package": package, "to": version, "export": kw.get("export"), "check": check})
        res = {"adr": adr["id"], "technology": adr["technology"], "package": package, "to": version, "repo": repo_name, "component": component,
               "with": [], "at": "t", "verdict": verdict, "verdict_text": tryout.VERDICTS[verdict], "steps": [], "checks": []}
        if export is not None:
            res["export"] = export
        return res
    return run


def test_run_dependency_shapes_result_and_writes_file():
    calls = []
    orig = tryout.tryout
    tryout.tryout = fake_tryout(calls, "pass", {"branch": "radar/tryout/zz-web-2.0.0", "pr_url": "https://example/pr/1", "skipped_reason": None})
    try:
        with tempfile.TemporaryDirectory() as d:
            _, p = adr_repo(d)
            out = Path(d) / "out"
            res = experiment.run(p["0001-dep.md"], out, export=True)
            saved = json.loads((out / "experiment.json").read_text(encoding="utf-8"))
    finally:
        tryout.tryout = orig
    assert calls == [{"repo": "app", "component": "api", "package": "zz-web", "to": "2.0.0", "export": True,
                      "check": {"python": "3.12", **CHECKS["app"]["components"]["api"]}}], calls
    assert res["verdict"] == "pass" and res["ok"] and res["recipe"] == "dependency" and res["summary"].startswith("의존성 올려 보기: 통과"), res
    assert res["report_md"].startswith("# 올려 보기: zz-web → 2.0.0") and res["export"]["pr_url"] == "https://example/pr/1", res
    assert saved["result"] == res and saved["plan"]["params"]["to"] == "2.0.0", saved


def test_run_failed_verdict_is_not_ok():
    calls = []
    orig = tryout.tryout
    tryout.tryout = fake_tryout(calls, "fail")
    try:
        with tempfile.TemporaryDirectory() as d:
            _, p = adr_repo(d)
            res = experiment.run(p["0001-dep.md"], Path(d) / "out")
    finally:
        tryout.tryout = orig
    assert res["verdict"] == "fail" and not res["ok"] and res["export"] is None, res


def test_unavailable_does_not_call_recipe():
    calls = []
    orig = tryout.tryout
    tryout.tryout = fake_tryout(calls)
    try:
        with tempfile.TemporaryDirectory() as d:
            _, p = adr_repo(d, files={"0003-model.md": MODEL})
            res = experiment.run(p["0003-model.md"], Path(d) / "out")
    finally:
        tryout.tryout = orig
    assert calls == [] and res["verdict"] == "unavailable" and not res["ok"] and experiment.NO_RECIPE in res["verdict_text"], res


def test_cost_gate_needs_confirm():
    calls = []
    orig, orig_cost = tryout.tryout, experiment.Dependency.cost
    tryout.tryout = fake_tryout(calls)
    experiment.Dependency.cost = "api"      # 유료 API 레시피라고 치고 — confirm 없이는 안 돈다
    try:
        with tempfile.TemporaryDirectory() as d:
            _, p = adr_repo(d)
            blocked = experiment.run(p["0001-dep.md"], Path(d) / "a")
            n = len(calls)
            allowed = experiment.run(p["0001-dep.md"], Path(d) / "b", confirm=True)
    finally:
        tryout.tryout, experiment.Dependency.cost = orig, orig_cost
    assert blocked["verdict"] == "needs_confirm" and n == 0 and "유료 API" in blocked["verdict_text"], blocked
    assert allowed["verdict"] == "pass" and len(calls) == 1, allowed


def test_stub_plans_for_real_but_does_not_run():
    calls = []
    orig = tryout.tryout
    tryout.tryout = fake_tryout(calls)
    try:
        with tempfile.TemporaryDirectory() as d:
            _, p = adr_repo(d, files={"0001-dep.md": DEP, "0003-model.md": MODEL})
            ok, no = experiment.stub(p["0001-dep.md"]), experiment.stub(p["0003-model.md"])
    finally:
        tryout.tryout = orig
    assert calls == [] and ok["stub"] and ok["verdict"] == "pass" and "[스텁]" in ok["report_md"] and "app · api" in ok["report_md"], ok
    assert no["stub"] and no["verdict"] == "unavailable", no


# ── 임베딩 레시피 (HYO-240) ──────────────────────────────────────────
SLOTS = ("version: 2\nslots:\n  - id: retrieval-dense\n    repo: app\n    component: arena\n"
         "  - id: gate-classifier\n    repo: app\n    component: api\n")
EMB = "---\nstatus: proposed\ntechnology: Acme/acme-embed-1\nuse: 신규 임베딩 모델 검토\naffected:\n  - {repo: app, component: arena}\n---\n# x\n"
EMB_EVID = ("---\nstatus: proposed\ntechnology: Acme Embed\nuse: 신규 임베딩 모델 검토\naffected:\n  - {repo: app, component: arena}\nevidence:\n"
            "  - {url: 'https://huggingface.co/blog/acme-embed', source_type: official_doc}\n"
            "  - {url: 'https://huggingface.co/Acme/acme-embed-2', source_type: official_doc}\n---\n# x\n")
EMB_NOID = "---\nstatus: proposed\ntechnology: Acme Embed\nuse: 신규 임베딩 모델 검토\naffected:\n  - {repo: app, component: arena}\n---\n# x\n"
EMB_OTHER = "---\nstatus: proposed\ntechnology: Acme/acme-llm\nuse: 신규 모델 검토\naffected:\n  - {repo: app, component: api}\n---\n# x\n"
EMB_DEP = "---\nstatus: proposed\ntechnology: zz-web\nuse: 의존성 릴리스 2.0.0 대응 (breaking)\naffected:\n  - {repo: app, component: arena}\n---\n# x\n"
FAKE_ARENA = """#!/usr/bin/env python3
import json, os, sys
a = sys.argv
out = a[a.index('--out') + 1]
os.makedirs(out, exist_ok=True)
open(os.path.join(os.path.dirname(out), 'args.json'), 'w').write(json.dumps(a[1:]))
if os.environ.get('FAKE_FAIL'):
    print('Traceback: 모델을 못 열었다')
    sys.exit(3)
v = os.environ.get('FAKE_VERDICT', 'better')
json.dump({'verdict': v}, open(os.path.join(out, 'report.json'), 'w'))
open(os.path.join(out, 'report.md'), 'w').write('# 가짜 비교 ' + v + '\\n')
"""


class _env:
    """환경 변수를 잠깐 바꾼다."""

    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        import os
        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        import os
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_embedding_recipe_picks_slot_and_model():
    files = {"0001-emb.md": EMB, "0002-evid.md": EMB_EVID, "0003-noid.md": EMB_NOID, "0004-other.md": EMB_OTHER, "0005-dep.md": EMB_DEP}
    with tempfile.TemporaryDirectory() as d, _env(RADAR_ARENA_PYTHON=sys.executable):
        root, p = adr_repo(d, files=files)
        (root / "radar-slots.yaml").write_text(SLOTS, encoding="utf-8")
        emb, evid, noid, other, dep = (experiment.plan(p[k]) for k in files)
        with _env(RADAR_ARENA_PYTHON=str(Path(d) / "없는-python")):
            noenv = experiment.plan(p["0001-emb.md"])
        (root / "radar-slots.yaml").unlink()
        noslots = experiment.plan(p["0001-emb.md"])
    assert emb["recipe"] == "embedding" and emb["ready"] and emb["cost"] == "local" and emb["exports"] is False, emb
    assert emb["params"] == {"model": "Acme/acme-embed-1"} and "gold@v1.2 test 36문항" in emb["detail"], emb
    assert evid["ready"] and evid["params"]["model"] == "Acme/acme-embed-2", evid                     # HF 블로그 주소는 모델 id가 아니다
    assert noid["recipe"] == "embedding" and not noid["ready"] and "HF 모델 id" in noid["reason"], noid   # 지어내지 않는다
    assert other["recipe"] is None and dep["recipe"] == "dependency" and dep["exports"] is True, (other, dep)   # 자리 밖 · 의존성 릴리스는 임베딩이 아니다
    assert not noenv["ready"] and "Arena 실험 환경" in noenv["reason"], noenv
    assert noslots["recipe"] is None, noslots                                                          # 자리 설정이 없으면 고르지 않는다


def test_embedding_run_reads_report_and_fails_honestly():
    if sys.platform == "win32":
        print("    (건너뜀: 가짜 Arena 환경이 셸 첫 줄(#!)을 쓴다)")
        return
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d) / "fake-python"
        fake.write_text(FAKE_ARENA, encoding="utf-8")
        fake.chmod(0o755)
        root, p = adr_repo(d, files={"0001-emb.md": EMB})
        (root / "radar-slots.yaml").write_text(SLOTS, encoding="utf-8")
        with _env(RADAR_ARENA_PYTHON=str(fake), FAKE_VERDICT="better", FAKE_FAIL=None):
            good = experiment.run(p["0001-emb.md"], Path(d) / "o1")
        args = json.loads((Path(d) / "o1" / "args.json").read_text(encoding="utf-8"))
        with _env(RADAR_ARENA_PYTHON=str(fake), FAKE_VERDICT="indistinguishable", FAKE_FAIL=None):
            same = experiment.run(p["0001-emb.md"], Path(d) / "o2", export=True)
        with _env(RADAR_ARENA_PYTHON=str(fake), FAKE_FAIL="1"):
            bad = experiment.run(p["0001-emb.md"], Path(d) / "o3")
    assert good["verdict"] == "better" and good["ok"] and good["report_md"].startswith("# 가짜 비교") and good["export"] is None, good
    assert args[:4] == ["-m", "arena.compare_embed", "--candidate", "Acme/acme-embed-1"] and args[-1] == str(Path(d).resolve()), args   # 형제 폴더 = ADR repo 옆
    assert same["verdict"] == "indistinguishable" and not same["ok"] and same["export"] is None, same      # 구분 안 됨은 「나음」이 아니다 · 내보내지 않는다
    assert bad["verdict"] == "error" and not bad["ok"] and "종료 3" in bad["verdict_text"] and "못 열었다" in bad["report_md"], bad


def _rows(base, ci, diff=0.04):
    return {"ALL": {"n": 36, "base": base, "cand": base + diff, "diff": diff, "ci95": ci, "wins": 1, "ties": 0, "losses": 0}}


def test_compare_embed_judging():
    """판정 기준 (사용자 2026-11-22): test 전체 95% 구간 · 지금 것 점수가 등록값과 다르면 판정 안 함 · 카드로 먼저 거르기."""
    from arena import compare_embed as ce
    assert ce.screen({"pipeline_tag": "text-ranking", "params": 5e8})[0] == "not_embedding"            # 리랭커를 임베딩으로 재지 않는다
    assert ce.screen({"pipeline_tag": "sentence-similarity", "params": 8e9})[0] == "too_big"           # 8B fp32 = 32GB
    assert ce.screen({"pipeline_tag": "feature-extraction", "params": 6e8}) == (None, None)
    assert ce.verdict_of(_rows(0.5791, [0.01, 0.2])) == "better" and ce.verdict_of(_rows(0.5791, [-0.2, -0.01], -0.1)) == "worse"
    assert ce.verdict_of(_rows(0.5791, [-0.03, 0.11])) == "indistinguishable"                           # 평균이 +여도 구간이 0을 포함하면 구분 안 됨
    assert ce.verdict_of(_rows(0.6000, [0.01, 0.2])) == "baseline_mismatch"                            # 지금 것이 등록값과 다르면 차이를 안 본다
    import random
    base, cand = {"q1": 0.5, "q2": 0.2, "q3": 1.0}, {"q1": 0.7, "q2": 0.2, "q3": 0.9}
    rows = ce.compare(base, cand, {"q1": "K", "q2": "K", "q3": "B"}, ["q1", "q2", "q3"], random.Random(1))
    assert rows["ALL"]["n"] == 3 and (rows["ALL"]["wins"], rows["ALL"]["ties"], rows["ALL"]["losses"]) == (1, 1, 1), rows
    assert rows["K"]["diff"] == 0.1 and rows["ALL"]["ci95"][0] <= rows["ALL"]["diff"] <= rows["ALL"]["ci95"][1], rows
    md = ce.report_md({"candidate": {"model": "Acme/x", "revision": "abc"}, "gold": "gold@v1.2", "verdict": "indistinguishable", "test": {"by_type": rows}})
    assert "구분 안 됨" in md and "| K | 2 |" in md and "채택 여부는 사람" in md, md


def test_compare_embed_cost_when_quality_ties():
    """품질이 구분 안 되면 결정은 비용으로 — 크기 · 문서 인코딩 속도 · 질의 지연 · 색인 크기를 지금 것과 견준 한 문장 (사용자 2026-11-22)."""
    from arena import compare_embed as ce
    arms = {"base": {"params": 568e6, "encode_per_s": 40.0, "query_ms_p50": 110.0, "vectors_mb": 15.8, "dim": 1024, "encode_sample": 256},
            "cand": {"params": 596e6, "encode_per_s": 20.0, "query_ms_p50": 130.0, "vectors_mb": 15.8, "dim": 1024, "encode_sample": 256}}
    s = ce.cost_summary(arms)
    assert s == "후보는 지금 것에 비해 모델 크기 1.05배 · 문서 인코딩 속도 0.50배 (느림) · 질의 지연 1.18배 (느림) · 색인 크기 1.00배", s
    assert ce.cost_summary({"base": {}, "cand": {}}) == "비용을 재지 못했다"
    tie = ce.report_md({"candidate": {"model": "Acme/x"}, "gold": "gold@v1.2", "verdict": "indistinguishable", "arms": arms})
    assert "품질로는 구분이 안 된다. **후보는" in tie and "| 후보 | 596M | 2.38GB | 1024 | 15.8MB | 20.0청크/초 | 130.0ms |" in tie, tie
    win = ce.report_md({"candidate": {"model": "Acme/x"}, "gold": "gold@v1.2", "verdict": "better", "arms": arms})
    assert "품질로는 구분이 안 된다" not in win and "후보는 지금 것에 비해" in win, win


MUTATIONS = [
    ("비용 문 없음", experiment, "run", lambda orig: (lambda adr_path, out, export=False, confirm=False, root=None: orig(adr_path, out, export, True, root)),
     lambda: test_cost_gate_needs_confirm()),
    ("임베딩: 평균만 보고 판정 (구간 무시)", "ce", "verdict_of", lambda orig: (lambda rows: "better" if rows["ALL"]["diff"] > 0 else "worse"),
     lambda: test_compare_embed_judging()),
    ("임베딩: 지금 것 점수를 확인 안 함", "ce", "verdict_of", lambda orig: (lambda rows: orig({"ALL": {**rows["ALL"], "base": 0.5791}})),
     lambda: test_compare_embed_judging()),
    ("임베딩: 카드로 안 거른다", "ce", "screen", lambda orig: (lambda info: (None, None)), lambda: test_compare_embed_judging()),
    ("임베딩: 자리 밖 결정도 고른다", experiment.Embedding, "match", lambda orig: (lambda self, adr, root=None: not experiment.DEP_USE.search(adr.get("use") or "")),
     lambda: test_embedding_recipe_picks_slot_and_model()),
    ("임베딩: 실패해도 판정을 지어낸다", experiment.Embedding, "run", lambda orig: (lambda self, adr, root, plan, out, export=False: {
        "verdict": "indistinguishable", "verdict_text": "구분 안 됨", "ok": False, "report_md": "", "export": None}),
     lambda: test_embedding_run_reads_report_and_fails_honestly()),
]


def run_mutations():
    from arena import compare_embed as ce
    caught = 0
    for name, owner, attr, make, test in MUTATIONS:
        owner = ce if owner == "ce" else owner
        orig = getattr(owner, attr)
        setattr(owner, attr, make(orig))
        try:
            try:
                test()
                print(f"  놓침  변이: {name}")
            except Exception:  # noqa: BLE001
                caught += 1
                print(f"  잡힘  변이: {name}")
        finally:
            setattr(owner, attr, orig)
    print(f"변이 {len(MUTATIONS)}개 중 {caught}개 잡힘")
    return caught == len(MUTATIONS)


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    ok = 0
    for n, f in tests:
        try:
            f()
            ok += 1
            print(f"  ok   {n}")
        except Exception:  # noqa: BLE001
            print(f"  FAIL {n}")
            traceback.print_exc()
    print(f"{ok}/{len(tests)} 통과")
    good = ok == len(tests) and run_mutations()
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())

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


def adr_repo(d, checks=True, files=None):
    root = Path(d) / "adr"
    (root / "docs" / "adr").mkdir(parents=True)
    (Path(d) / "app").mkdir()
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
        assert "app · api" in pl["detail"] and pl["export_rule"].startswith("통과"), pl


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


MUTATIONS = [
    ("비용 문 없음", "run", lambda orig: (lambda adr_path, out, export=False, confirm=False, root=None: orig(adr_path, out, export, True, root))),
]


def run_mutations():
    caught = 0
    for name, attr, make in MUTATIONS:
        orig = getattr(experiment, attr)
        setattr(experiment, attr, make(orig))
        try:
            try:
                test_cost_gate_needs_confirm()
                print(f"  놓침  변이: {name}")
            except Exception:  # noqa: BLE001
                caught += 1
                print(f"  잡힘  변이: {name}")
        finally:
            setattr(experiment, attr, orig)
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

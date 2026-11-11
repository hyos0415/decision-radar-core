"""같은 주 재생 러너(pipeline/replay.py, HYO-164) 오프라인 테스트 — 모델 · 네트워크 없음, 임시 git repo만.

  python pipeline/tests/test_replay.py

등록(comparison-protocol §7 「같은 주 재생」 + 변경 2026-11-11)이 요구하는 것 중 코드가 집행하는 것을 본다. 규율 3 — 잡혀야 할 것이 잡히는지 변이로:
- 날짜 기준 basis: 대상 레포를 「그 시각 이전 기본 브랜치 마지막 커밋」으로 읽는다. 지금 HEAD로 읽는 길(basis.remote_tree)이면 다른 버전이 나와 테스트가 잡는다
- 원격 추적 기록이 그 시각을 못 덮으면(stale) 멈춘다 · 원본 repo에는 아무것도 쓰지 않는다
- 격리: 보호 repo에 파일이 생기거나 · HEAD가 움직이거나 · 행동 로그(events.jsonl)가 늘면 Guard가 잡는다. 출력 폴더가 보호 폴더 안이면 거부
- 자식 환경에서 합집합 · 내보내기 · 실제 폴더 env를 지운다
- 제품 코드가 고정 커밋과 다르면 실제 실행을 거부한다 (러너 자신 파일은 빼고)
- 등급 파일을 그 주 스택으로 자르고 retired를 푼다
- 대조: 「제품이 냄」 = 3번 중 2번 이상 · 같은 항목 = 패키지 + 주 버전(v5.0.0 = v5.0.2) · 스킬만 항목의 갈린 단계 여섯
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import basis, replay as rp  # noqa: E402

AT = "2026-10-12T00:38:38Z"


def git(cwd, *args, when=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    p = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def commit(repo, when, files):
    for path, text in files.items():
        f = Path(repo) / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", when, when=when)
    return git(repo, "rev-parse", "HEAD")


def origin_and_clone(td):
    """origin(커밋 셋: 10/1 · 10/10 · 10/15) → 작업 사본 local(원격 추적 기록이 있다)."""
    origin = Path(td) / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    c1 = commit(origin, "2026-10-01T00:00:00Z", {"requirements.txt": "langfuse==4.1.0\n"})
    c2 = commit(origin, "2026-10-10T00:00:00Z", {"requirements.txt": "langfuse==4.2.0\n"})
    c3 = commit(origin, "2026-10-15T00:00:00Z", {"requirements.txt": "langfuse==5.0.0\n"})
    local = Path(td) / "local"
    git(td, "clone", "-q", str(origin), str(local))
    return origin, local, (c1, c2, c3)


def state(repo):
    return git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain", "--untracked-files=all")


def raises(fn, exc=rp.ReplayError):
    try:
        fn()
    except exc as e:
        return str(e)
    raise AssertionError(f"{exc.__name__}가 나야 한다")


# ---------------------------------------------------------------------------- 날짜 기준 basis

def test_dated_commit_picks_last_before():
    with tempfile.TemporaryDirectory() as td:
        _, local, (c1, c2, c3) = origin_and_clone(td)
        d = rp.dated_commit(local, AT)
        assert d["commit"] == c2 and d["fresh"] and d["branch"].endswith("origin/main"), d
        assert rp.dated_commit(local, "2026-10-20T00:00:00Z")["commit"] == c3      # 변이: 시각을 옮기면 다른 커밋
        assert rp.dated_commit(local, "2026-09-01T00:00:00Z")["commit"] is None


def test_materialize_dated_reads_that_week():
    """basis.materialize(tree=dated_tree) — 그 주 버전(4.2.0)을 읽는다. 지금 HEAD로 읽으면(remote_tree) 5.0.0 — 그 차이를 잡는다."""
    with tempfile.TemporaryDirectory() as td:
        _, local, (_, c2, _) = origin_and_clone(td)
        before = state(local)
        loc = Path(td) / "locator.json"
        loc.write_text(json.dumps([{"repo": "r", "path": str(local)}]), encoding="utf-8")
        derived, b = basis.materialize(loc, Path(td) / "cache", tree=rp.dated_tree(AT))
        r = b["repos"][0]
        assert r["source"] == "dated" and r["commit"] == c2 and r["versions"] == {"langfuse": ["4.2.0"]}, r
        path = json.loads(Path(derived).read_text(encoding="utf-8"))[0]["path"]
        assert Path(path) != local and (Path(path) / "requirements.txt").read_text(encoding="utf-8") == "langfuse==4.2.0\n"
        _, now = basis.materialize(loc, Path(td) / "cache-now")              # 변이: 날짜 없이 원격 HEAD
        assert now["repos"][0]["versions"] == {"langfuse": ["5.0.0"]}
        assert state(local) == before                                       # 원본 작업 사본은 그대로
        _, again = basis.materialize(loc, Path(td) / "cache", tree=rp.dated_tree("2026-10-05T00:00:00Z"))   # 같은 캐시를 다른 주로
        assert again["repos"][0]["versions"] == {"langfuse": ["4.1.0"]}


def test_stale_refused():
    """원격에 그 시각 전 커밋이 더 있어도 로컬 기록이 모르면(끝 < 시각, fetch도 그 전) 지금 것으로 넘어가지 않고 멈춘다."""
    with tempfile.TemporaryDirectory() as td:
        origin, local, (_, _, c3) = origin_and_clone(td)
        c4 = commit(origin, "2026-10-18T00:00:00Z", {"requirements.txt": "langfuse==5.1.0\n"})   # local은 아직 모른다
        at = "2026-10-20T00:00:00Z"
        fh = Path(local) / ".git" / "FETCH_HEAD"
        if fh.exists():
            fh.unlink()
        assert not rp.dated_commit(local, at)["fresh"]                       # 기록 끝 10/15 < 시각 · fetch 기록 없음
        msg = raises(lambda: rp.dated_tree(at)("r", local, Path(td) / "cache"))
        assert "fetch" in msg
        assert rp.dated_tree(at, allow_stale=True)("r", local, Path(td) / "cache2")["commit"] == c3
        fh.write_text("", encoding="utf-8")
        os.utime(fh, (1792000000, 1792000000))                               # 2026-10-14 무렵 fetch — 시각 전이라 덮지 못한다
        assert not rp.dated_commit(local, at)["fresh"]
        git(local, "fetch", "-q")                                            # 지금 fetch — 10/18 커밋을 알게 되고 기록이 시각을 덮는다
        d = rp.dated_commit(local, at)
        assert d["fresh"] and d["commit"] == c4, d


def test_export_tree_reads_only():
    with tempfile.TemporaryDirectory() as td:
        origin, _, (c1, _, _) = origin_and_clone(td)
        before = state(origin)
        dest = rp.export_tree(origin, c1, Path(td) / "x")
        assert (dest / "requirements.txt").read_text(encoding="utf-8") == "langfuse==4.1.0\n" and not (dest / ".git").exists()
        assert state(origin) == before


def test_human_decision_first_change():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        git(repo, "init", "-q", "-b", "main")
        p = "docs/adr/0016-x.md"
        d = commit(repo, "2026-10-26T00:00:00Z", {p: "---\nstatus: proposed\n---\n# x\n"})
        assert rp.human_decision(repo, p, d)["status"] == "proposed"
        commit(repo, "2026-10-26T01:00:00Z", {p: "---\nstatus: proposed\n---\n# x 고침\n"})   # 검토 중 고쳐도 아직 proposed
        e = commit(repo, "2026-10-27T00:00:00Z", {p: "---\nstatus: adopted\n---\n# x\n"})
        commit(repo, "2026-10-28T00:00:00Z", {p: "---\nstatus: superseded\n---\n# x\n"})
        h = rp.human_decision(repo, p, d)
        assert h["status"] == "adopted" and h["commit"] == e, h                # 처음 바뀐 것 — 나중 상태가 아니다


# ---------------------------------------------------------------------------- 격리

def test_guard_catches_writes():
    with tempfile.TemporaryDirectory() as td:
        repo, data, fence = Path(td) / "adr", Path(td) / "data", Path(td) / "target"
        repo.mkdir()
        data.mkdir()
        fence.mkdir()
        git(repo, "init", "-q", "-b", "main")
        commit(repo, "2026-10-01T00:00:00Z", {"README.md": "x\n"})
        (data / "events.jsonl").write_text('{"event": "a"}\n', encoding="utf-8")
        g = rp.Guard([repo], [data], [fence])
        g.check()
        (repo / "stray.md").write_text("y", encoding="utf-8")              # 변이 1: 보호 repo에 파일
        raises(g.check, rp.IsolationError)
        (repo / "stray.md").unlink()
        g.check()
        with open(data / "events.jsonl", "a", encoding="utf-8") as f:      # 변이 2: 행동 로그에 한 줄
            f.write('{"event": "b"}\n')
        raises(g.check, rp.IsolationError)
        g = rp.Guard([repo], [data], [fence])
        commit(repo, "2026-10-02T00:00:00Z", {"README.md": "z\n"})          # 변이 3: 커밋(HEAD가 움직임)
        raises(g.check, rp.IsolationError)
        g = rp.Guard([repo], [data], [fence])
        (fence / "a.txt").write_text("작업 중", encoding="utf-8")            # 대상 레포 작업 사본은 상태를 견주지 않는다 (다른 세션)
        g.check()
        for bad in (repo / "out", data / "out", fence / "x" / "out", repo):
            raises(lambda: g.check_out(bad), rp.IsolationError)
        g.check_out(Path(td) / "replay-out")


def test_child_env_strips():
    env = rp.child_env({"RADAR_UNION": "on", "RADAR_EXPORT": "git", "RADAR_SNAPSHOT_REPO": "/real", "RADAR_APP_DATA": "/d", "PATH": "p"},
                       {"RADAR_TARGETS": Path("t.json")})
    assert "RADAR_UNION" not in env and "RADAR_EXPORT" not in env and "RADAR_SNAPSHOT_REPO" not in env and "RADAR_APP_DATA" not in env
    assert env["PATH"] == "p" and env["RADAR_TARGETS"] == "t.json" and env["PYTHONUTF8"] == "1"


def test_code_drift():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        git(repo, "init", "-q", "-b", "main")
        c = commit(repo, "2026-11-03T00:00:00Z", {"pipeline/gate.py": "a\n", "pipeline/replay.py": "r\n", "docs/x.md": "d\n"})
        assert rp.code_drift(repo, c) == []
        (repo / "pipeline" / "replay.py").write_text("r2\n", encoding="utf-8")   # 러너 자신 — 괜찮다
        (repo / "docs" / "x.md").write_text("d2\n", encoding="utf-8")             # 제품 밖 — 괜찮다
        assert rp.code_drift(repo, c) == []
        (repo / "pipeline" / "gate.py").write_text("b\n", encoding="utf-8")       # 변이: 제품 코드 수정(커밋 전)
        assert rp.code_drift(repo, c) == ["pipeline/gate.py"]


# ---------------------------------------------------------------------------- 등급 파일

def test_prune_tiers():
    data = {"version": 1, "technologies": {"langfuse": {"technology": "langfuse"}, "networkx": {"technology": "networkx"}},
            "overrides": {"litellm": {"technology": "litellm", "retired": "2026-11-04"}, "ranx": {"technology": "ranx", "path": "dev"},
                          "langfuse": {"technology": "langfuse", "tiers": ["L3"]}}}
    out, rep = rp.prune_tiers(data, {"langfuse", "litellm"})
    assert set(out["technologies"]) == {"langfuse"} and set(out["overrides"]) == {"litellm", "langfuse"}
    assert "retired" not in out["overrides"]["litellm"] and rep["unretired"] == ["litellm"]
    assert rep["removed"] == ["networkx"] and rep["removed_overrides"] == ["ranx"]
    assert "retired" in data["overrides"]["litellm"]                       # 원본은 그대로


# ---------------------------------------------------------------------------- 대조

def dep(pkg, ver, repo):
    return {"id": f"github-release:{repo}@{ver}", "source": "github", "title": f"{pkg} {ver}",
            "url": f"https://github.com/{repo}/releases/tag/{ver}", "meta": {"dependency": pkg, "tag": ver, "repo": repo}}


def test_attribute_stages():
    lf, fa, vl = dep("langfuse", "v5.0.0", "langfuse/langfuse-python"), dep("fastapi", "0.143.0", "fastapi/fastapi"), dep("vllm", "v0.31.0", "vllm-project/vllm")
    rec = lambda it, end, stage=None: {"id": it["id"], "end": end, "stops": [{"stage": stage, "name": "n", "note": "왜"}] if stage else []}  # noqa: E731
    key = lambda it: rp.union.corpus_key(it)  # noqa: E731
    assert rp.attribute(key(fa), [], [fa], {}, [])["stage"] == "수집 시점"
    assert rp.attribute(key(fa), [], [], {}, [])["stage"] == "출처에 없음"
    assert rp.attribute(key(lf), [lf], [], {lf["id"]: rec(lf, "out", "2c")}, [])["stage"] == "관문 탈락"
    assert rp.attribute(key(lf), [lf], [], {lf["id"]: rec(lf, "list", "3")}, [])["stage"] == "상한에서 잘림"
    skip = {"skipped_existing": [{"url": vl["url"], "adr": "0012"}]}
    draft = {"candidates": [{"technology": "vLLM", "source_urls": [vl["url"]]}]}
    excl = {"excluded": [{"url": vl["url"], "reason": "우리 버전에 해당 없음"}]}
    trace = {vl["id"]: rec(vl, "detect")}
    assert rp.attribute(key(vl), [vl], [], trace, [skip, skip, draft])["stage"] == "이미 결정됨"
    r = rp.attribute(key(vl), [vl], [], trace, [excl, excl, {}])
    assert r["stage"] == "모델이 뺌" and "우리 버전에 해당 없음" in r["detail"] and "absent" in r["detail"]
    # 같은 주 버전의 여러 릴리스 중 가장 멀리 간 것으로 본다 (v5.0.2가 detect까지, v5.0.0은 접힘)
    lf2 = dep("langfuse", "v5.0.2", "langfuse/langfuse-python")
    r = rp.attribute(key(lf), [lf, lf2], [], {lf["id"]: rec(lf, "list", "3"), lf2["id"]: rec(lf2, "detect")}, [excl])
    assert r["stage"] == "모델이 뺌", r


ADR = """---
status: proposed
date: {date}
technology: {tech}
use: 의존성 릴리스 {ver} 대응
affected:
- repo: r
  component: c
evidence:
- url: {url}
  source_type: release_note
---
# {tech} {ver} 대응이 필요한지
"""


def write_draft(d, name, tech, ver, url, date="2026-10-19"):
    Path(d).mkdir(parents=True, exist_ok=True)
    (Path(d) / name).write_text(ADR.format(tech=tech, ver=ver, url=url, date=date), encoding="utf-8")


def test_compare_week_majority():
    """한 주 끝까지: 스킬 Langfuse v5.0.0 ↔ 제품 v5.0.2 2/3 = 둘 다 · 제품 pydantic 2/3 = 제품만 · 제품 torch 1/3 = 냄 아님 ·
    스킬 FastAPI(코퍼스 없음, 다음 주 있음) = 스킬만 「수집 시점」. 변이: 제품이 1번만 냈으면 Langfuse는 스킬만으로 내려간다."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        snaps = td / "snaps"
        lf, lf2, pd, tc = (dep("langfuse", "v5.0.0", "langfuse/langfuse-python"), dep("langfuse", "v5.0.2", "langfuse/langfuse-python"),
                           dep("pydantic", "v3.0.0", "pydantic/pydantic"), dep("torch", "v3.1.0", "pytorch/pytorch"))
        fa = dep("fastapi", "0.143.0", "fastapi/fastapi")
        for wk, items in (("2026-W42", [lf, lf2, pd, tc]), ("2026-W43", [fa])):
            (snaps / wk).mkdir(parents=True)
            (snaps / wk / "items.jsonl").write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
        cfg = {"out": str(td / "out"), "snapshots": str(snaps), "adr_repo": str(td / "adr")}
        w = rp.Week("2026-W42", cfg)
        rp.write_json(w.dir / "week.json", {"week": "2026-W42", "at": "2026-10-19T00:39:50Z", "now": "2026-10-19T09:39:50+09:00", "pr": 13,
                                            "next": "2026-W43", "slots_profile": {"note": "n"}, "fetch_links": False})
        write_draft(w.dir / "skill" / "drafts", "0015-langfuse.md", "Langfuse", "Python SDK v5.0.0", lf["url"])
        write_draft(w.dir / "skill" / "drafts", "0099-fastapi.md", "FastAPI", "0.143.0", fa["url"])
        rp.write_json(w.dir / "skill" / "decisions.json", [{"file": "0015-langfuse.md", "status": "adopted"}, {"file": "0099-fastapi.md", "status": "rejected"}])
        feed = [{"id": x["id"], "title": x["title"], "kind": "dependency_release", "package": x["meta"]["dependency"], "version": x["meta"]["tag"],
                 "url": x["url"], "source_type": "release_note"} for x in (lf2, pd, tc)]
        rp.write_json(w.feed / "feed.json", feed)
        rp.write_json(w.feed / "gate.json", {})

        def run(k, drafts):
            d = w.dir / "detect" / f"k{k}"
            files = []
            for i, (tech, it) in enumerate(drafts):
                name = f"00{20 + i}-{tech.lower()}.md"
                write_draft(d / "adr" / "docs" / "adr", name, tech, it["meta"]["tag"], it["url"])
                files.append({"adr": name[:4], "file": name, "technology": tech, "source_urls": [it["url"]]})
            rp.write_json(d / "summary.json", {"drafts": files, "p3_errors": []})
            rp.write_json(d / "report.json", {"candidates": [{"technology": t, "source_urls": [it["url"]]} for t, it in drafts]})
        run(1, [("Langfuse", lf2), ("pydantic", pd)])
        run(2, [("Langfuse", lf2), ("torch", tc)])
        run(3, [("pydantic", pd)])
        r = rp.compare_week(w, 3)
        assert [x["technology"] for x in r["both"]] == ["Langfuse"] and r["both"][0]["product_runs"] == [1, 2], r["both"]
        assert [x["technology"] for x in r["product_only"]] == ["pydantic"] and r["product_only"][0]["n"] == 2
        assert [x["technology"] for x in r["product_once"]] == ["torch"]
        assert [(x["technology"], x["stage"]) for x in r["skill_only"]] == [("FastAPI", "수집 시점")]
        assert r["both"][0]["decision"]["status"] == "adopted" and r["product_only"][0]["route"] == "관문"
        md = rp.render({"weeks": [r]})
        assert "합집합 3" in md and "**수집 시점**" in md
        # 변이: k2에서 Langfuse를 빼면 1/3 — 「냄」이 아니라 스킬만으로
        run(2, [("torch", tc)])
        r = rp.compare_week(w, 3)
        assert [x["technology"] for x in r["skill_only"]] == ["FastAPI", "Langfuse"] or [x["technology"] for x in r["skill_only"]] == ["Langfuse", "FastAPI"]
        assert not r["both"]


def test_weeks_registry():
    """등록 §7 「대상」 그대로 — 10/12 ↔ W41 · 10/19 ↔ W42 · 10/26 ↔ W43, 다음 주는 「수집 시점」 판정용."""
    assert list(rp.WEEKS) == ["2026-W41", "2026-W42", "2026-W43"]
    assert [rp.WEEKS[w]["pr"] for w in rp.WEEKS] == [8, 13, 16]
    assert [rp.WEEKS[w]["next"] for w in rp.WEEKS] == ["2026-W42", "2026-W43", "2026-W44"]
    assert rp.K == 3 and rp.MIN_RUNS == 2 and rp.CODE == "4eab096"


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    bad = 0
    for n, f in TESTS:
        try:
            f()
            print(f"  ok   {n}")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  FAIL {n}")
            traceback.print_exc()
    print(f"{len(TESTS) - bad}/{len(TESTS)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

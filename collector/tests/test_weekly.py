"""주간 사슬(collector/weekly.py) · launchd plist(schedule_mac.py) 오프라인 테스트 — 가짜 러너 · 임시 git repo + 로컬 bare 원격만 쓴다.

  python collector/tests/test_weekly.py
"""
import argparse
import contextlib
import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector import schedule_mac, weekly  # noqa: E402

WEEK = "2026-W44"
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, f"git {args}: {p.stderr}"
    return p.stdout


class World:
    """임시 부모 폴더: radar-snapshots(복제본 + bare 원격) · 레포 자리 · 가짜 러너."""

    def __init__(self, fail=(), feed_json=True, revisit_index=True):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        seed = self.tmp / "seed"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(seed)], check=True, capture_output=True)
        self._cfg(seed)
        (seed / WEEK).mkdir()
        (seed / WEEK / "manifest.json").write_text("{}", encoding="utf-8")
        (seed / "tiers.json").write_text("{}", encoding="utf-8")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "push", "-q", "origin", "HEAD:main")
        self.snaps = self.tmp / "radar-snapshots"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.snaps)], check=True, capture_output=True)
        self._cfg(self.snaps)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        self.out = self.tmp / "out"
        self.pout = self.tmp / "pout"
        self.fail = set(fail)
        self.feed_json = feed_json
        self.revisit_index = revisit_index
        self.calls = []
        self.env = {"RADAR_SNAPSHOT_REPO": str(self.snaps), "RADAR_PROFILE": str(self.tmp / "p.yaml"), "RADAR_TARGETS": str(self.tmp / "t.json"),
                    "RADAR_ADR_DIR": str(self.tmp / "adr"), "RADAR_TIERS": str(self.snaps / "tiers.json")}

    @staticmethod
    def _cfg(d):
        for k, v in (("user.name", "radar-test"), ("user.email", "radar@example.invalid"), ("commit.gpgsign", "false")):
            git(d, "config", k, v)

    def runner(self, module, args, log):
        self.calls.append((module, list(args)))
        if module in self.fail:
            return 1
        if module == "pipeline.detect_feed" and self.feed_json:
            out = Path(args[args.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "feed.json").write_text("[]", encoding="utf-8")
            (out / "index.json").write_text("{}", encoding="utf-8")
        if module == "pipeline.revisit_feed" and self.revisit_index:
            out = Path(args[args.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            (out / "index.json").write_text("{}", encoding="utf-8")
        return 0

    def go(self, *argv, runner=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = weekly.main(list(argv), runner=runner or self.runner, env=self.env, root=self.root, out_dir=self.out, pipeline_out=self.pout)
        return rc, buf.getvalue()

    def mods(self):
        return [m for m, _ in self.calls]

    def summary(self):
        return json.loads((self.out / "last-weekly.json").read_text(encoding="utf-8"))

    def remote_files(self):
        return git(self.remote, "ls-tree", "-r", "--name-only", "main").split()


def arg(call, flag):
    return call[1][call[1].index(flag) + 1]


def test_order_and_args():
    w = World()
    rc, _ = w.go("--no-push")
    assert rc == 0, rc
    assert w.mods() == ["collector.run", "collector.snapshot", "pipeline.importance", "pipeline.profile_filter", "pipeline.detect_feed", "pipeline.revisit_feed"], w.mods()
    gate = w.calls[3]
    assert arg(gate, "--snapshot") == str(w.snaps / WEEK)
    assert arg(gate, "--gate") == "gpt-6.1-sol" and "--real-api" in gate[1]
    assert arg(gate, "--tiers") == str(w.snaps / "tiers.json")
    assert arg(gate, "--out") == str(w.pout / WEEK)
    assert arg(gate, "--profile") == w.env["RADAR_PROFILE"] and arg(gate, "--targets") == w.env["RADAR_TARGETS"] and arg(gate, "--adr-dir") == w.env["RADAR_ADR_DIR"]
    feed = w.calls[4]
    assert arg(feed, "--run") == str(w.pout / WEEK) and arg(feed, "--out") == str(w.snaps / "detect" / WEEK)
    assert w.calls[1][1][-2:] == ["freeze", "--push"]
    rv = w.calls[5]
    assert arg(rv, "--adr-dir") == w.env["RADAR_ADR_DIR"] and arg(rv, "--snapshots") == str(w.snaps) and arg(rv, "--week") == WEEK
    assert arg(rv, "--out") == str(w.snaps / "revisit" / WEEK)


def test_slots_step_runs_when_profile_exists_and_failure_continues():
    """HYO-168 — 자리 프로필(ADR repo radar-slots.yaml)이 있으면 관문 다음에 자리 질의를 같은 run 폴더로 돌린다.
    실패해도(구독 claude -p) detect 입력은 관문 몫으로 게시한다. 프로필이 없으면 건너뛴다 (위 test_order_and_args)."""
    w = World()
    (w.tmp / "adr").mkdir()
    (w.tmp / "adr" / "radar-slots.yaml").write_text("version: 1\nslots: []\n", encoding="utf-8")
    rc, _ = w.go("--no-push")
    assert rc == 0 and w.mods()[3:6] == ["pipeline.profile_filter", "pipeline.slots", "pipeline.detect_feed"], w.mods()
    sl = w.calls[4]
    assert sl[1][0] == "run" and arg(sl, "--snapshot") == str(w.snaps / WEEK) and arg(sl, "--out") == str(w.pout / WEEK)
    assert arg(sl, "--slots") == str(w.tmp / "adr" / "radar-slots.yaml")
    w2 = World(fail={"pipeline.slots"})
    (w2.tmp / "adr").mkdir()
    (w2.tmp / "adr" / "radar-slots.yaml").write_text("version: 1\nslots: []\n", encoding="utf-8")
    rc, _ = w2.go()
    assert rc == 0 and f"detect/{WEEK}/feed.json" in w2.remote_files()
    assert [s["ok"] for s in w2.summary()["steps"] if s["name"] == "slots"] == [False]


def test_health_step_alerts_after_freeze_and_failure_continues():
    """HYO-172 — 얼릴 때 쓴 <주>/source-health.json이 있으면 얼림 다음에 Linear 알림을 부른다. 실패해도 사슬은 간다.
    파일이 없으면(얼릴 때 검사가 죽었다) 건너뛴다 (위 test_order_and_args)."""
    w = World(fail={"collector.health"})
    (w.snaps / WEEK / "source-health.json").write_text(json.dumps({"line": "출처 고장 1 — google-gemini"}, ensure_ascii=False), encoding="utf-8")
    rc, _ = w.go()
    assert rc == 0 and w.mods()[1:3] == ["collector.snapshot", "collector.health"], w.mods()
    assert w.calls[2][1] == ["--snapshots", str(w.snaps), "alert", "--week", WEEK], w.calls[2]
    st = next(s for s in w.summary()["steps"] if s["name"] == "health")
    assert not st["ok"] and st["detail"].startswith("출처 고장 1 — google-gemini") and f"detect/{WEEK}/feed.json" in w.remote_files(), st
    w2 = World()
    rc, _ = w2.go("--no-push")
    st = next(s for s in w2.summary()["steps"] if s["name"] == "health")
    assert rc == 0 and st["skipped"] and "collector.health" not in w2.mods(), st


def test_skill_step_off_by_default_and_before_gate_when_on():
    """HYO-167 합집합 — 스킬 단계는 기본 꺼짐(구독 세션 0). RADAR_UNION=on이면 관문 앞(D6)에서 같은 run 폴더의 union/으로 돌고,
    실패해도 관문 · detect 입력은 수집 항목만으로 간다. 이미 게시된 주에는 세션을 쓰지 않는다."""
    w = World()
    rc, _ = w.go("--no-push")
    assert rc == 0 and "pipeline.union" not in w.mods()
    assert [s["skipped"] for s in w.summary()["steps"] if s["name"] == "skill"] == [True]
    w = World()
    w.env["RADAR_UNION"] = "on"
    rc, _ = w.go("--no-push")
    assert rc == 0 and w.mods()[3:5] == ["pipeline.union", "pipeline.profile_filter"], w.mods()
    un = w.calls[3]
    assert un[1][0] == "run" and arg(un, "--week") == WEEK and arg(un, "--snapshots") == str(w.snaps)
    assert arg(un, "--out") == str(w.pout / WEEK / "union") and arg(un, "--adr") == str(Path(w.env["RADAR_ADR_DIR"]).parent.parent)  # <ADR repo>/docs/adr → repo
    w2 = World(fail={"pipeline.union"})
    w2.env["RADAR_UNION"] = "on"
    rc, _ = w2.go()
    assert rc == 0 and f"detect/{WEEK}/feed.json" in w2.remote_files()
    assert [s["ok"] for s in w2.summary()["steps"] if s["name"] == "skill"] == [False]
    w3 = World()
    w3.env["RADAR_UNION"] = "on"
    w3.go()                                    # 게시까지
    w3.calls.clear()
    w3.go()
    assert "pipeline.union" not in w3.mods(), w3.mods()


def test_targets_remote_head_and_basis():
    """HYO-162 — 등급·관문은 원격 HEAD 사본을 가리키는 대상 목록을 받고, 관문 뒤 run 폴더에 판정 기준이 남는다."""
    w = World()
    bare, app = w.tmp / "app.git", w.tmp / "app"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(app)], check=True, capture_output=True)
    World._cfg(app)
    (app / "package.json").write_text(json.dumps({"dependencies": {"electron": "^45.0.0"}}), encoding="utf-8")
    git(app, "add", "-A")
    git(app, "commit", "-q", "-m", "app")
    git(app, "push", "-q", "origin", "HEAD:main")
    Path(w.env["RADAR_TARGETS"]).write_text(json.dumps([{"repo": "app", "path": "app"}]), encoding="utf-8")
    rc, _ = w.go("--no-push")
    assert rc == 0, rc
    derived = str(w.out / "targets" / "radar-targets.json")
    assert arg(w.calls[2], "--targets") == derived and arg(w.calls[3], "--targets") == derived, w.calls[2:4]
    b = json.loads((w.pout / WEEK / "basis.json").read_text(encoding="utf-8"))
    assert b["repos"][0]["source"] == "remote" and b["repos"][0]["commit"] == git(bare, "rev-parse", "main").strip(), b
    assert b["repos"][0]["versions"] == {"electron": ["45.0.0"]}
    assert w.summary()["targets"].startswith("판정 기준: 원격 HEAD 1개"), w.summary()


def test_targets_unreadable_keeps_original():
    w = World()  # RADAR_TARGETS 파일이 없다 — 예전처럼 원래 경로로 돌고 그렇다고 남긴다
    rc, out = w.go("--no-push")
    assert rc == 0 and arg(w.calls[3], "--targets") == w.env["RADAR_TARGETS"]
    assert "대상 목록을 못 읽었다" in w.summary()["targets"] and "[targets]" in out
    assert not (w.pout / WEEK / "basis.json").exists()


def test_gate_override():
    w = World()
    w.go("--no-push", "--gate", "stub")
    assert arg(w.calls[3], "--gate") == "stub"
    w2 = World()
    w2.env["RADAR_GATE"] = "gpt-6-luna"
    w2.go("--no-push")
    assert arg(w2.calls[3], "--gate") == "gpt-6-luna"


def test_collect_fail_still_freezes():
    w = World(fail={"collector.run"})
    rc, _ = w.go("--no-push")
    assert rc == 0 and "collector.snapshot" in w.mods()
    assert w.summary()["steps"][0]["ok"] is False


def test_freeze_fail_stops():
    w = World(fail={"collector.snapshot"})
    rc, _ = w.go()
    assert rc == 1 and w.mods() == ["collector.run", "collector.snapshot"], w.mods()


def test_tiers_fail_continues():
    w = World(fail={"pipeline.importance"})
    rc, _ = w.go("--no-push")
    assert rc == 0 and "pipeline.detect_feed" in w.mods()
    assert [s["ok"] for s in w.summary()["steps"] if s["name"] == "tiers"] == [False]


def test_gate_fail_stops():
    w = World(fail={"pipeline.profile_filter"})
    rc, out = w.go()
    assert rc == 1 and "pipeline.detect_feed" not in w.mods(), w.mods()
    names = [s["name"] for s in w.summary()["steps"]]
    assert "feed" not in names and "publish" not in names, names
    assert "관문" in out
    assert not (w.snaps / "detect").exists() and w.remote_files() == [f"{WEEK}/manifest.json", "tiers.json"]


def test_feed_fail_stops():
    w = World(fail={"pipeline.detect_feed"})
    rc, _ = w.go()
    assert rc == 1 and "publish" not in [s["name"] for s in w.summary()["steps"]]
    w2 = World(feed_json=False)                     # 종료 코드는 0이어도 feed.json이 없으면 실패
    rc, _ = w2.go()
    assert rc == 1 and "publish" not in [s["name"] for s in w2.summary()["steps"]]


def test_publish_pushes_only_detect():
    w = World()
    (w.snaps / "stray.txt").write_text("x", encoding="utf-8")        # 추적 안 되는 다른 파일 — 올라가면 안 된다
    rc, _ = w.go()
    assert rc == 0
    files = w.remote_files()
    assert f"detect/{WEEK}/feed.json" in files and f"revisit/{WEEK}/index.json" in files and "stray.txt" not in files, files
    assert git(w.remote, "log", "-1", "--format=%s", "main").strip() == f"detect 입력 {WEEK} (corpus@{WEEK} → 관문 → detect_feed)"


def test_publish_refuses_stray_staged():
    w = World()
    (w.snaps / "other.txt").write_text("x", encoding="utf-8")
    git(w.snaps, "add", "other.txt")
    rc, out = w.go()
    assert rc == 1, out
    assert w.remote_files() == [f"{WEEK}/manifest.json", "tiers.json"]
    assert git(w.snaps, "diff", "--cached", "--name-only").strip() == ""      # 스테이징을 풀었다
    assert w.summary()["steps"][-1]["name"] == "publish" and w.summary()["steps"][-1]["ok"] is False


def test_second_run_skips():
    w = World()
    assert w.go()[0] == 0
    before = git(w.remote, "rev-parse", "main")
    w.calls.clear()
    rc, out = w.go("--from", "gate", "--week", WEEK)
    assert rc == 0 and w.mods() == [], w.mods()                       # 유료 관문도 안 돈다
    assert "이미 만듦" in out
    assert git(w.remote, "rev-parse", "main") == before
    assert all(s["skipped"] for s in w.summary()["steps"])


def test_other_machine_published():
    """다른 기기가 같은 주를 이미 게시했다 — 받아 보고 건너뛴다 (유료 관문 0, 게시 충돌 없음)."""
    w = World()
    other = w.tmp / "other"
    subprocess.run(["git", "clone", "-q", str(w.remote), str(other)], check=True, capture_output=True)
    w._cfg(other)
    (other / "detect" / WEEK).mkdir(parents=True)
    (other / "detect" / WEEK / "feed.json").write_text("[]", encoding="utf-8")
    (other / "revisit" / WEEK).mkdir(parents=True)
    (other / "revisit" / WEEK / "index.json").write_text("{}", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "other machine")
    git(other, "push", "-q", "origin", "HEAD:main")
    before = git(w.remote, "rev-parse", "main")
    rc, out = w.go("--from", "gate", "--week", WEEK)
    assert rc == 0 and w.mods() == [], (w.mods(), out)
    assert "이미 만듦" in out and git(w.remote, "rev-parse", "main") == before


def test_revisit_fail_still_publishes_detect():
    """revisit 출처(네트워크)가 실패해도 detect 입력은 게시한다 — 종료 코드 0, revisit 단계만 실패로 남는다 (HYO-159)."""
    for w in (World(fail={"pipeline.revisit_feed"}), World(revisit_index=False)):
        rc, _ = w.go()
        assert rc == 0, rc
        files = w.remote_files()
        assert f"detect/{WEEK}/feed.json" in files and not any(f.startswith("revisit/") for f in files), files
        assert [s["ok"] for s in w.summary()["steps"] if s["name"] == "revisit"] == [False]


def test_revisit_added_to_published_week():
    """detect만 게시된 주 — 다시 돌리면 유료 관문 없이 revisit 출처만 받아 따로 커밋한다 (HYO-159 도입 주, 또는 지난번 revisit 실패)."""
    w = World(revisit_index=False)
    assert w.go()[0] == 0
    w.revisit_index = True
    w.calls.clear()
    rc, out = w.go("--from", "gate", "--week", WEEK)
    assert rc == 0 and w.mods() == ["pipeline.revisit_feed"], (w.mods(), out)
    assert f"revisit/{WEEK}/index.json" in w.remote_files()
    assert git(w.remote, "log", "-1", "--format=%s", "main").strip() == f"revisit 출처 {WEEK} (ADR source_hint → revisit_feed)"
    assert git(w.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main").split() == [f"revisit/{WEEK}/index.json"]


def test_step_log_not_scheduler_log():
    """단계 로그는 예약 작업 콘솔 로그와 다른 파일이어야 한다 — Windows에서 PowerShell `*>`가 잡은 파일에 쓰면 PermissionError (10/26 등록 직후 발견)."""
    ps1 = (Path(weekly.REPO) / "collector" / "schedule_windows.ps1").read_text(encoding="utf-8-sig")
    console = {"last-weekly.log"} if "last-$label.log" in ps1 else set()
    pl = schedule_mac.build(Path("/r"), "weekly", "Tuesday", "16:00")
    console.add(Path(pl["StandardOutPath"]).name)
    assert console == {"last-weekly.log"}, console
    assert weekly.STEP_LOG not in console, weekly.STEP_LOG
    if sys.platform != "win32":
        return
    # 예약 작업과 같은 모양으로 재현: PowerShell이 콘솔 로그를 *>로 잡은 채 실제 run_step이 단계 로그에 쓴다
    d = Path(tempfile.mkdtemp())
    (d / "fakemod.py").write_text("print('step ran')", encoding="utf-8")
    (d / "go.py").write_text(f"import sys; sys.path.insert(0, {str(weekly.REPO)!r}); from collector import weekly; from pathlib import Path\n"
                             f"sys.exit(weekly.run_step('fakemod', [], Path({str(d)!r}) / weekly.STEP_LOG, cwd={str(d)!r}))", encoding="utf-8")
    p = subprocess.run(["powershell", "-NoProfile", "-Command", f"& '{sys.executable}' '{d / 'go.py'}' *> '{d / 'last-weekly.log'}'; exit $LASTEXITCODE"],
                       capture_output=True, text=True)
    assert p.returncode == 0, (d / "last-weekly.log").read_text(encoding="utf-8", errors="replace")[-400:]
    assert "step ran" in (d / weekly.STEP_LOG).read_text(encoding="utf-8")


def test_from_gate_week():
    w = World()
    rc, _ = w.go("--from", "gate", "--week", WEEK, "--no-push")
    assert rc == 0 and w.mods() == ["pipeline.profile_filter", "pipeline.detect_feed", "pipeline.revisit_feed"], w.mods()
    s = w.summary()
    assert s["week"] == WEEK and [x["skipped"] for x in s["steps"][:3]] == [True, True, True]


def test_no_push_leaves_remote():
    w = World()
    before = git(w.remote, "rev-parse", "main")
    rc, _ = w.go("--no-push")
    assert rc == 0 and git(w.remote, "rev-parse", "main") == before
    assert git(w.snaps, "log", "-1", "--format=%s").startswith("detect 입력")      # 로컬 커밋은 있다


def test_summary_shape():
    w = World()
    w.go("--no-push")
    s = w.summary()
    assert set(s) == {"week", "started", "finished", "steps", "targets"} and s["week"] == WEEK
    assert [x["name"] for x in s["steps"]] == weekly.STEPS
    assert all(set(x) == {"name", "ok", "skipped", "seconds", "detail"} for x in s["steps"])


def test_dry_run():
    w = World()
    rc, out = w.go("--dry-run")
    assert rc == 0 and w.calls == [] and not (w.out / "last-weekly.json").exists()
    assert "collector.weekly" not in out and "profile_filter" in out and "detect_feed" in out and "--real-api" in out and "revisit_feed" in out


def test_real_runner_subprocess():
    """실제 run_step이 모듈을 -m으로 돌리고 로그에 이어 쓴다 (가짜 모듈 — 네트워크 없음)."""
    d = Path(tempfile.mkdtemp())
    (d / "fakemod.py").write_text("import sys; print('hi', sys.argv[1:]); sys.exit(3)", encoding="utf-8")
    log = d / "l.log"
    rc = weekly.run_step("fakemod", ["a"], log, cwd=d)
    assert rc == 3 and "hi ['a']" in log.read_text(encoding="utf-8")
    weekly.run_step("fakemod", ["b"], log, cwd=d)
    assert log.read_text(encoding="utf-8").count("hi") == 2


def _ns(**kw):
    d = dict(weekly_day="Tuesday", weekly_time="16:00", aws_day="Thursday", aws_time="16:00")
    d.update(kw)
    return argparse.Namespace(**d)


def test_plists():
    repo = Path("/r/jhs-decisions")
    pl = schedule_mac.plists(_ns(), repo)
    w, a = pl["weekly"], pl["aws"]
    assert w["StartCalendarInterval"] == {"Weekday": 2, "Hour": 16, "Minute": 0}, w["StartCalendarInterval"]
    assert a["StartCalendarInterval"] == {"Weekday": 4, "Hour": 16, "Minute": 0}
    assert w["ProgramArguments"] == [str(repo / ".venv" / "bin" / "python"), "-m", "collector.weekly"]
    assert a["ProgramArguments"][1:] == ["-m", "collector.run", "--label", "aws", "--sources", "vendor", "--only", "aws-bedrock"]
    assert w["Label"] == "com.decision-radar.collector-weekly" and a["Label"] == "com.decision-radar.collector-aws"
    assert w["WorkingDirectory"] == str(repo)
    assert w["StandardOutPath"] == str(repo / "collector" / "out" / "last-weekly.log") == w["StandardErrorPath"]
    assert a["StandardOutPath"].endswith("last-aws.log")
    assert plistlib.loads(plistlib.dumps(w)) == w
    m = schedule_mac.plists(_ns(weekly_day="sunday", weekly_time="07:05"), repo)["weekly"]["StartCalendarInterval"]
    assert m == {"Weekday": 0, "Hour": 7, "Minute": 5}


def test_install_refused_off_darwin():
    if sys.platform == "darwin":
        return
    try:
        schedule_mac.main(["--install"])
    except SystemExit as e:
        assert "macOS" in str(e)
    else:
        raise AssertionError("darwin이 아닌데 설치가 진행됐다")


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception:
            bad += 1
            print(f"FAIL {n}\n{traceback.format_exc()}")
    print(f"{len(tests) - bad}/{len(tests)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

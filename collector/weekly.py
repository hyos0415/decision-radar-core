"""주간 사슬 한 줄 — 수집 → 얼림 → 등급 → 관문 → detect 입력 → 게시 (HYO-99 3단계). Windows·macOS 같은 코드다.

  python -m collector.weekly [--from {collect,freeze,tiers,gate,feed,publish}] [--week YYYY-Www] [--gate 백엔드] [--no-push] [--dry-run]

실패 규칙: 수집 실패 → 그래도 얼린다(죽은 출처도 manifest에 남는다) · 얼림 실패 → 멈춤 · 등급 실패 → 계속 (키 없음은 weekly-tiers가 이미 건너뜀)
          · 관문 실패(예: OPENAI_API_KEY 없음) → 멈춤, feed·게시 없음 · feed 실패 → 멈춤
          · 게시: radar-snapshots에 detect/<주>/가 이미 커밋돼 있으면 「이미 만듦」 — 덮어쓰지 않는다 (얼린 주를 고치지 않는 것과 같다).
            관문(유료 API)도 그 경우엔 돌리지 않는다.
게시 경로 radar-snapshots/detect/<YYYY-Www>/feed.json 은 데스크톱 앱(desktop/backend/service.py _latest_feed)과의 고정 계약이다.
출력: collector/out/last-weekly-steps.log (단계 출력, 이어 쓰기) · last-weekly.json (요약). 종료 코드 0 = 멈춘 실패 없음.
예약 작업의 콘솔 출력(last-weekly.log)과 파일을 나눈다 — Windows는 PowerShell이 `*>`로 잡고 있는 파일에 자식이 쓰면 PermissionError다.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector import snapshot  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STEP_LOG = "last-weekly-steps.log"  # 예약 작업 콘솔 로그(last-weekly.log)와 달라야 한다 — 위 머리말
STEPS = ["collect", "freeze", "tiers", "gate", "feed", "publish"]
DEFAULT_GATE = "gpt-6.1-sol"


def run_step(module, args, log, cwd=REPO):
    """모듈을 자식 프로세스로 돌려 종료 코드를 돌려준다 — 테스트는 이 함수를 바꿔 끼운다."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with open(log, "ab") as f:
        f.write(f"\n=== {dt.datetime.now().isoformat(timespec='seconds')} python -m {module} {' '.join(args)}\n".encode("utf-8"))
        f.flush()
        return subprocess.run([sys.executable, "-m", module, *args], cwd=str(cwd), env=env, stdout=f, stderr=subprocess.STDOUT).returncode


def paths(env=None, root=REPO):
    """경로 해석은 pipeline/importance/cli.py와 같다: env > <이 레포 부모>/..."""
    env = os.environ if env is None else env
    parent = Path(root).parent
    return {
        "snapshots": Path(env.get("RADAR_SNAPSHOT_REPO") or parent / "radar-snapshots"),
        "profile": Path(env.get("RADAR_PROFILE") or parent / "adr" / "radar-profile.yaml"),
        "targets": Path(env.get("RADAR_TARGETS") or parent / "adr" / "radar-targets.json"),
        "adr_dir": Path(env.get("RADAR_ADR_DIR") or parent / "adr" / "docs" / "adr"),
        "tiers": Path(env.get("RADAR_TIERS") or parent / "radar-snapshots" / "tiers.json"),
    }


def gate_args(snap, gate, p, out):
    return ["run", "--snapshot", str(snap), "--gate", gate, "--tiers", str(p["tiers"]), "--real-api",
            "--profile", str(p["profile"]), "--targets", str(p["targets"]), "--adr-dir", str(p["adr_dir"]), "--out", str(out)]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")


def committed(repo, week):
    p = _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", f"detect/{week}")
    return p.returncode == 0 and bool(p.stdout.strip())


def publish(repo, week, no_push):
    """detect/<주>/만 커밋·push한다. (ok, detail)"""
    prefix = f"detect/{week}/"
    if not (Path(repo) / "detect" / week / "feed.json").exists():
        return False, f"{prefix}feed.json 이 없다"
    if not no_push:
        p = _git(repo, "pull", "--ff-only", "-q")
        if p.returncode != 0:
            return False, f"git pull --ff-only 실패: {p.stderr.strip()}"
    p = _git(repo, "add", "--", f"detect/{week}")
    if p.returncode != 0:
        return False, f"git add 실패: {p.stderr.strip()}"
    staged = [x for x in _git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if x]
    stray = [x for x in staged if not x.startswith(prefix)]
    if stray:
        _git(repo, "reset", "-q")
        return False, f"{prefix} 밖의 경로가 스테이징돼 있어 거부했다: {stray[:5]}"
    if not staged:
        return True, "바뀐 것 없음"
    msg = f"detect 입력 {week} (corpus@{week} → 관문 → detect_feed)"
    p = _git(repo, "commit", "-q", "-m", msg)
    if p.returncode != 0:
        return False, f"git commit 실패: {p.stderr.strip() or p.stdout.strip()}"
    if no_push:
        return True, f"커밋만 함 (--no-push): {msg}"
    p = _git(repo, "push", "-q", "origin", "HEAD")
    if p.returncode != 0:
        return False, f"git push 실패: {p.stderr.strip()}"
    return True, msg


def run(a, runner=run_step, env=None, root=REPO, out_dir=None, pipeline_out=None):
    p = paths(env, root)
    out_dir = Path(out_dir or Path(root) / "collector" / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / STEP_LOG
    gate = a.gate or (os.environ if env is None else env).get("RADAR_GATE") or DEFAULT_GATE
    start = STEPS.index(a.start)
    summary = {"week": a.week, "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "finished": None, "steps": []}
    state = {"stopped": False, "done": False}

    def record(name, ok, detail="", skipped=False, t0=None):
        summary["steps"].append({"name": name, "ok": ok, "skipped": skipped, "seconds": round(time.time() - t0, 1) if t0 else 0, "detail": detail})
        print(f"[{name}] {'건너뜀' if skipped else ('ok' if ok else '실패')} {detail}".rstrip())

    def stop(why):
        state["stopped"] = True
        print(f"멈춤: {why}", file=sys.stderr)

    for i, name in enumerate(STEPS):
        if state["stopped"]:
            break
        if i < start:
            record(name, True, "건너뜀 (--from)", skipped=True)
            continue
        t0 = time.time()
        if name == "collect":
            rc = runner("collector.run", ["--label", "weekly"], log)
            record(name, rc == 0, f"종료 코드 {rc}" + (" — 그래도 얼린다" if rc else ""), t0=t0)
        elif name == "freeze":
            rc = runner("collector.snapshot", ["--repo", str(p["snapshots"]), "freeze", "--push"], log)
            record(name, rc == 0, f"종료 코드 {rc}", t0=t0)
            if rc != 0:
                stop("얼림 실패 — 이후 단계를 하지 않는다")
        elif name == "tiers":
            rc = runner("pipeline.importance", ["weekly-tiers", "--report", str(out_dir / "last-tiers.json"),
                                                "--tiers", str(p["tiers"]), "--targets", str(p["targets"])], log)
            record(name, rc == 0, f"종료 코드 {rc}" + (" — 계속" if rc else ""), t0=t0)
        else:
            if not summary["week"]:
                weeks = snapshot.frozen_weeks(p["snapshots"])
                if not weeks:
                    record(name, False, f"얼린 주가 없다: {p['snapshots']}", t0=t0)
                    stop("주를 정할 수 없다")
                    continue
                summary["week"] = weeks[-1]
            week = summary["week"]
            run_out = Path(pipeline_out or Path(root) / "pipeline" / "out") / week
            feed_out = p["snapshots"] / "detect" / week
            if name == "gate":
                if not a.no_push:  # 다른 기기가 이미 게시했는지는 받아 봐야 안다 — 모르고 돌리면 유료 관문을 또 부르고 게시에서 엇갈린다
                    pr = _git(p["snapshots"], "pull", "--ff-only", "-q")
                    if pr.returncode != 0:
                        record(name, False, f"git pull --ff-only 실패: {pr.stderr.strip()}", t0=t0)
                        stop("radar-snapshots를 받지 못했다 — 이미 만들었는지 모른 채 유료 관문을 돌리지 않는다")
                        continue
                if committed(p["snapshots"], week):
                    state["done"] = True
                    record(name, True, f"이미 만듦: detect/{week}/ 가 커밋돼 있다 (유료 관문을 다시 돌리지 않는다)", skipped=True)
                    continue
                if not (p["snapshots"] / week).exists():
                    record(name, False, f"얼린 주 폴더가 없다: {p['snapshots'] / week}", t0=t0)
                    stop("관문 입력이 없다")
                    continue
                rc = runner("pipeline.profile_filter", gate_args(p["snapshots"] / week, gate, p, run_out), log)
                record(name, rc == 0, f"백엔드 {gate}" if rc == 0 else f"관문({gate}) 실패, 종료 코드 {rc} — OPENAI_API_KEY(.env) · 모델 이름 · {log} 를 본다", t0=t0)
                if rc != 0:
                    stop("관문 실패 — feed·게시를 하지 않는다")
            elif name == "feed":
                if state["done"]:
                    record(name, True, "이미 만듦", skipped=True)
                    continue
                rc = runner("pipeline.detect_feed", ["--run", str(run_out), "--out", str(feed_out)], log)
                ok = rc == 0 and (feed_out / "feed.json").exists()
                record(name, ok, str(feed_out) if ok else f"종료 코드 {rc}" + ("" if rc else " — feed.json 이 없다"), t0=t0)
                if not ok:
                    stop("feed 실패 — 게시를 하지 않는다")
            else:
                if state["done"] or committed(p["snapshots"], week):
                    record(name, True, f"이미 만듦: detect/{week}/", skipped=True)
                    continue
                ok, detail = publish(p["snapshots"], week, a.no_push)
                record(name, ok, detail, t0=t0)
                if not ok:
                    stop(detail)
    summary["finished"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    (out_dir / "last-weekly.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 1 if state["stopped"] else 0


def dry_run(a, env=None, root=REPO):
    p = paths(env, root)
    gate = a.gate or (os.environ if env is None else env).get("RADAR_GATE") or DEFAULT_GATE
    wk = a.week or "<주: 얼린 뒤 최신>"
    run_out = Path(root) / "pipeline" / "out" / wk
    py = sys.executable
    cmds = {
        "collect": f"{py} -m collector.run --label weekly",
        "freeze": f"{py} -m collector.snapshot --repo {p['snapshots']} freeze --push",
        "tiers": f"{py} -m pipeline.importance weekly-tiers --report {Path(root) / 'collector' / 'out' / 'last-tiers.json'} --tiers {p['tiers']} --targets {p['targets']}",
        "gate": f"{py} -m pipeline.profile_filter " + " ".join(gate_args(p["snapshots"] / wk, gate, p, run_out)),
        "feed": f"{py} -m pipeline.detect_feed --run {run_out} --out {p['snapshots'] / 'detect' / wk}",
        "publish": f"git -C {p['snapshots']} pull --ff-only; add detect/{wk}; commit; " + ("(push 없음)" if a.no_push else "push"),
    }
    for name in STEPS[STEPS.index(a.start):]:
        print(f"{name}: {cmds[name]}")
    print("(--dry-run: 실행하지 않았다)")
    return 0


def main(argv=None, **kw):
    ap = argparse.ArgumentParser(prog="collector.weekly", description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="start", choices=STEPS, default="collect", help="이 단계부터 (앞 단계는 건너뜀)")
    ap.add_argument("--week", help="YYYY-Www (기본: 얼린 뒤 가장 최신 주)")
    ap.add_argument("--gate", help=f"관문 백엔드 (기본: env RADAR_GATE 또는 {DEFAULT_GATE})")
    ap.add_argument("--no-push", action="store_true", help="게시를 커밋까지만 (원격에 올리지 않는다)")
    ap.add_argument("--dry-run", action="store_true", help="계획한 명령만 출력하고 끝낸다")
    a = ap.parse_args(argv)
    if a.dry_run:
        return dry_run(a, kw.get("env"), kw.get("root", REPO))
    return run(a, **kw)


if __name__ == "__main__":
    sys.exit(main())

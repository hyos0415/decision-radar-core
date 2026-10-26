"""수집기 정기 실행을 macOS launchd에 등록한다 (schedule_windows.ps1과 같은 두 작업, HYO-99 3단계).

  python -m collector.schedule_mac --print                       # plist만 출력 (기본, 어느 OS에서나)
  python -m collector.schedule_mac --install --weekly-day Tuesday --weekly-time 16:00 --aws-day Thursday --aws-time 16:00
  python -m collector.schedule_mac --remove

- weekly = `python -m collector.weekly` (수집 → 얼림 → 등급 → 관문 → detect 입력 → 게시) · aws = AWS What's New만
- launchd StartCalendarInterval: 잠자기 중 놓친 실행은 깨어날 때 돈다. Weekday는 일요일=0 (화=2, 목=4)
- 파이썬은 <레포>/.venv/bin/python (anthropic·openai가 거기 있다). 출력: collector/out/last-<label>.log
"""
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
LABELS = {"weekly": "com.decision-radar.collector-weekly", "aws": "com.decision-radar.collector-aws"}


def parse_day(s):
    try:
        return DAYS[s.strip().lower()]
    except KeyError:
        raise SystemExit(f"요일을 모르겠다: {s} (Sunday~Saturday)")


def parse_time(s):
    try:
        h, m = (int(x) for x in s.split(":"))
    except ValueError:
        raise SystemExit(f"시각 형식은 HH:MM: {s}")
    if not (0 <= h < 24 and 0 <= m < 60):
        raise SystemExit(f"시각 범위를 벗어났다: {s}")
    return h, m


def build(repo, kind, day, at):
    """plist 사전 하나. kind = weekly | aws."""
    repo = Path(repo)
    py = str(repo / ".venv" / "bin" / "python")
    args = {"weekly": [py, "-m", "collector.weekly"],
            "aws": [py, "-m", "collector.run", "--label", "aws", "--sources", "vendor", "--only", "aws-bedrock"]}[kind]
    h, m = parse_time(at)
    log = str(repo / "collector" / "out" / f"last-{kind}.log")
    return {
        "Label": LABELS[kind],
        "ProgramArguments": args,
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {"PYTHONIOENCODING": "utf-8"},
        "StartCalendarInterval": {"Weekday": parse_day(day), "Hour": h, "Minute": m},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def plists(a, repo=REPO):
    return {"weekly": build(repo, "weekly", a.weekly_day, a.weekly_time), "aws": build(repo, "aws", a.aws_day, a.aws_time)}


def agents_dir():
    return Path.home() / "Library" / "LaunchAgents"


def _launchctl(*args):
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(a, repo=REPO):
    if sys.platform != "darwin":
        raise SystemExit("--install은 macOS에서만 된다 (launchd). 다른 OS는 --print로 plist만 본다")
    (Path(repo) / "collector" / "out").mkdir(parents=True, exist_ok=True)
    if not (Path(repo) / ".venv" / "bin" / "python").exists():
        raise SystemExit(f".venv가 없다: {Path(repo) / '.venv' / 'bin' / 'python'}")
    agents_dir().mkdir(parents=True, exist_ok=True)
    dom = f"gui/{os.getuid()}"
    for kind, pl in plists(a, repo).items():
        f = agents_dir() / f"{pl['Label']}.plist"
        _launchctl("bootout", f"{dom}/{pl['Label']}")            # 이미 있으면 갱신 — 없으면 실패해도 된다
        f.write_bytes(plistlib.dumps(pl))
        p = _launchctl("bootstrap", dom, str(f))
        if p.returncode != 0:
            raise SystemExit(f"launchctl bootstrap 실패({kind}): {p.stderr.strip()}")
        print(f"registered: {pl['Label']} ({kind}) -> {f}")


def remove():
    if sys.platform != "darwin":
        raise SystemExit("--remove는 macOS에서만 된다")
    dom = f"gui/{os.getuid()}"
    for label in LABELS.values():
        _launchctl("bootout", f"{dom}/{label}")
        (agents_dir() / f"{label}.plist").unlink(missing_ok=True)
    print(f"removed: {', '.join(LABELS.values())}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="collector.schedule_mac", description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--print", dest="do_print", action="store_true", help="plist 출력 (기본)")
    g.add_argument("--install", action="store_true")
    g.add_argument("--remove", action="store_true")
    ap.add_argument("--weekly-day", default="Tuesday")
    ap.add_argument("--weekly-time", default="16:00")
    ap.add_argument("--aws-day", default="Thursday")
    ap.add_argument("--aws-time", default="16:00")
    a = ap.parse_args(argv)
    if a.install:
        install(a)
    elif a.remove:
        remove()
    else:
        for kind, pl in plists(a).items():
            print(f"<!-- {kind} -->\n{plistlib.dumps(pl).decode('utf-8')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

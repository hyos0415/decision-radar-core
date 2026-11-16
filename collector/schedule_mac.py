"""수집기 정기 실행을 macOS launchd에 등록한다 (schedule_windows.ps1과 같은 두 작업, HYO-99 3단계).

  python -m collector.schedule_mac --print                       # plist만 출력 (기본, 어느 OS에서나)
  python -m collector.schedule_mac --check [--call]              # 예약 실행과 같은 환경으로 미리 확인 (등록 안 함)
  python -m collector.schedule_mac --install --weekly-day Tuesday --weekly-time 16:00 --aws-day Thursday --aws-time 16:00
  python -m collector.schedule_mac --remove

- weekly = `python -m collector.weekly` (수집 → 얼림 → 등급 → 관문 → detect 입력 → 게시) · aws = AWS What's New만
- launchd StartCalendarInterval: 잠자기 중 놓친 실행은 깨어날 때 돈다. Weekday는 일요일=0 (화=2, 목=4)
- 파이썬은 <레포>/.venv/bin/python (anthropic·openai가 거기 있다). 출력: collector/out/last-<label>.log
- launchd는 로그인 셸의 PATH를 읽지 않는다 (`/usr/bin:/bin:/usr/sbin:/sbin`만). 그래서 설치할 때 `claude` 절대 경로를 찾아
  plist에 `RADAR_CLAUDE_BIN`과 PATH(claude 폴더 + Homebrew · 시스템 경로)를 넣는다 — 자리 질의 · 관심사 질의(지금)와
  관문(W48부터, HYO-169)이 구독 `claude -p`를 쓴다. 못 찾으면 설치를 거부한다 (HYO-182)
- `--check`는 launchd가 주는 최소 환경 + plist의 EnvironmentVariables로 claude · 구독 로그인 · git 원격 · 대상 폴더를 확인한다.
  `--call`을 붙이면 Haiku `claude -p` 한 번(구독 세션 1개)까지 부른다
"""
import argparse
import json
import os
import plistlib
import posixpath
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DAYS = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
LABELS = {"weekly": "com.decision-radar.collector-weekly", "aws": "com.decision-radar.collector-aws"}
BASE_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
LAUNCHD_KEEP = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "SSH_AUTH_SOCK")   # launchd가 사용자 작업에 주는 것 — 나머지 셸 변수는 없다
SUBSCRIPTION = "claude.ai"                                      # desktop/backend/engine_status.py와 같다
CHECK_MODEL = "claude-haiku-5-5"


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


def find_claude(env=None):
    """지금 셸에서 claude 절대 경로 — RADAR_CLAUDE_BIN이 있으면 그것. 없으면 None."""
    env = os.environ if env is None else env
    return env.get("RADAR_CLAUDE_BIN") or shutil.which("claude", path=env.get("PATH"))


def require_claude(env=None):
    c = find_claude(env)
    if not c:
        raise SystemExit("claude CLI를 못 찾았다 — 자리 · 관심사 질의(지금)와 관문(W48부터)이 구독 claude -p를 쓴다. "
                         "claude를 설치 · 로그인한 셸에서 다시 돌리거나 RADAR_CLAUDE_BIN=<절대 경로>를 준다")
    return c


def launch_env(claude=None):
    """plist EnvironmentVariables. claude 폴더를 PATH 맨 앞에 — npm으로 깐 claude는 같은 폴더의 node를 찾는다."""
    head = posixpath.dirname(claude) if claude else ""            # 빈 칸은 PATH에서 「현재 폴더」라 넣지 않는다
    dirs = ([head] if head else []) + list(BASE_PATH)
    env = {"PYTHONIOENCODING": "utf-8", "PATH": ":".join(dict.fromkeys(dirs))}
    if claude:
        env["RADAR_CLAUDE_BIN"] = claude
    return env


def build(repo, kind, day, at, claude=None):
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
        "EnvironmentVariables": launch_env(claude),
        "StartCalendarInterval": {"Weekday": parse_day(day), "Hour": h, "Minute": m},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }


def plists(a, repo=REPO, claude=None):
    return {"weekly": build(repo, "weekly", a.weekly_day, a.weekly_time, claude),
            "aws": build(repo, "aws", a.aws_day, a.aws_time, claude)}


def check_env(claude=None, base=None):
    """예약 실행과 같은 환경 — launchd가 주는 최소 변수 + plist의 EnvironmentVariables. 셸의 API 키 · RADAR_* 는 없다."""
    base = os.environ if base is None else base
    return {**{k: base[k] for k in LAUNCHD_KEEP if k in base}, **launch_env(claude)}


def git_remotes(repo=REPO):
    """예약 실행이 당기거나 미는 git 사본 — radar-snapshots(관문 전 pull · 게시 push) · adr · 미러 · 수집 전용 대상."""
    from collector import run
    parent = Path(repo).parent
    roots = {"radar-snapshots": parent / "radar-snapshots", "adr": parent / "adr"}
    roots.update(run.my_mirror_roots())
    roots.update(run.collect_only_targets()[1])
    return roots


def missing_targets():
    from collector import run
    return [t["repo"] for t in run.my_targets_missing() + run.collect_only_targets()[2]["missing"]]


def check(repo=REPO, call=False, runner=subprocess.run, base=None):
    """[(항목, 통과, 설명)] — 등록하지 않는다."""
    claude = find_claude(base)
    env = check_env(claude, base)
    rows = [("claude 경로", bool(claude), claude or "못 찾음 — RADAR_CLAUDE_BIN=<절대 경로>")]

    def probe(argv, timeout=60):
        try:
            p = runner(argv, env=env, cwd=str(repo), capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, str(e)[:160]
        lines = (p.stdout or p.stderr or "").strip().splitlines()
        return p, (lines[-1] if lines else "")[:160]

    if claude:
        p, out = probe([claude, "--version"])
        rows.append(("claude --version", p is not None and p.returncode == 0, out))
        p, out = probe([claude, "auth", "status", "--json"])
        try:
            method = json.loads(p.stdout).get("authMethod") if p is not None and p.returncode == 0 else None
        except (ValueError, AttributeError):
            method = None
        rows.append(("구독 로그인", method == SUBSCRIPTION, f"authMethod={method}" if method else out))
        if call:
            p, out = probe([claude, "-p", "--model", CHECK_MODEL, "ok라고만 답해"], timeout=180)
            rows.append((f"{CHECK_MODEL} 호출 (구독 세션 1)", p is not None and p.returncode == 0 and bool(out), out))
    for name, root in git_remotes(repo).items():
        if not Path(root).is_dir():
            rows.append((f"git 원격: {name}", False, f"폴더 없음 {root}"))
            continue
        p, out = probe(["git", "-C", str(root), "ls-remote", "--heads", "origin"])
        rows.append((f"git 원격: {name}", p is not None and p.returncode == 0, "읽기 OK" if p is not None and p.returncode == 0 else out))
    miss = missing_targets()
    rows.append(("대상 폴더", not miss, "모두 있음" if not miss else "없음: " + ", ".join(miss)))
    return rows


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
    claude = require_claude()
    agents_dir().mkdir(parents=True, exist_ok=True)
    dom = f"gui/{os.getuid()}"
    for kind, pl in plists(a, repo, claude).items():
        f = agents_dir() / f"{pl['Label']}.plist"
        _launchctl("bootout", f"{dom}/{pl['Label']}")            # 이미 있으면 갱신 — 없으면 실패해도 된다
        f.write_bytes(plistlib.dumps(pl))
        p = _launchctl("bootstrap", dom, str(f))
        if p.returncode != 0:
            raise SystemExit(f"launchctl bootstrap 실패({kind}): {p.stderr.strip()}")
        print(f"registered: {pl['Label']} ({kind}) -> {f}")
    print(f"claude: {claude} (plist RADAR_CLAUDE_BIN · PATH)")


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
    g.add_argument("--check", action="store_true", help="예약 실행과 같은 환경으로 미리 확인 (등록 안 함)")
    g.add_argument("--install", action="store_true")
    g.add_argument("--remove", action="store_true")
    ap.add_argument("--call", action="store_true", help="--check와 함께: Haiku claude -p 한 번 (구독 세션 1개)")
    ap.add_argument("--weekly-day", default="Tuesday")
    ap.add_argument("--weekly-time", default="16:00")
    ap.add_argument("--aws-day", default="Thursday")
    ap.add_argument("--aws-time", default="16:00")
    a = ap.parse_args(argv)
    if a.install:
        install(a)
    elif a.remove:
        remove()
    elif a.check:
        if sys.platform != "darwin":
            raise SystemExit("--check는 macOS에서만 뜻이 있다 — 다른 OS는 최소 환경이 달라(Windows는 SystemRoot 등) 틀린 실패가 난다")
        rows = check(call=a.call)
        for name, ok, detail in rows:
            print(f"{'OK ' if ok else 'NG '} {name} — {detail}")
        bad = sum(1 for _, ok, _ in rows if not ok)
        print(f"{len(rows) - bad}/{len(rows)} 통과" + ("" if a.call else " (claude -p 호출은 --call)"))
        return 1 if bad else 0
    else:
        claude = find_claude()
        if not claude:
            print("<!-- claude를 못 찾았다 — --install은 거부된다 (RADAR_CLAUDE_BIN=<절대 경로>) -->")
        for kind, pl in plists(a, claude=claude).items():
            print(f"<!-- {kind} -->\n{plistlib.dumps(pl).decode('utf-8')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

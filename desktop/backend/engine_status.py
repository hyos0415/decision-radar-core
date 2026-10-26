"""구독 CLI 엔진 상태 · 로그인 창 (HYO-99 3단계 — `GET /engine` · `POST /engine/login`). 화면 버튼은 HYO-139(프론트).

- 상태는 `claude --version` · `claude auth status --json`을 읽기만 한다 (사용량 0). API 키는 env에서 지운다 — 남아 있으면 구독 로그인이 가려진다
- 로그인은 앱이 하지 않는다 — OS 터미널을 열어 `claude auth login --claudeai`를 사람이 마친다. 앱은 로그인 코드·토큰을 받지도 보지도 않는다
  (HYO-114 약관 · OAuth 토큰 직접 사용 금지). 끝나면 화면이 `GET /engine`을 다시 부른다
- 「준비됨」은 구독(claude.ai) 로그인일 때만 — 다른 방식(콘솔 API 키 등)이면 `claude -p`가 API로 과금되니 준비 안 됨으로 본다
- 터미널 열기는 OS마다 다르다: Windows 새 콘솔(cmd /k) · macOS `.command` 파일을 Terminal로(osascript 자동화 권한 창이 안 뜬다) ·
  Linux x-terminal-emulator, 없으면 열지 않고 칠 명령만 돌려준다
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # engines.STRIP_ENV와 같은 이유
LOGIN_ARGS = ("auth", "login", "--claudeai")
SUBSCRIPTION = "claude.ai"
TIMEOUT = 20
CREATE_NEW_CONSOLE = 0x00000010  # subprocess.CREATE_NEW_CONSOLE — Windows 밖에서도 테스트하려고 숫자로


def find_bin():
    return os.environ.get("RADAR_CLAUDE_BIN") or shutil.which("claude")


def _argv(bin_path):
    return [sys.executable, bin_path] if bin_path.endswith(".py") else [bin_path]  # .py = 테스트용 가짜 CLI (engines와 같은 약속)


def _env():
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(PYTHONIOENCODING="utf-8", NO_COLOR="1")
    return env


def _call(bin_path, *args):
    """(종료 코드, stdout) — 실행 못 하면 (None, 이유)."""
    try:
        p = subprocess.run([*_argv(bin_path), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=TIMEOUT, env=_env(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, f"{TIMEOUT}초 안에 끝나지 않았다"
    except OSError as e:
        return None, f"실행 못 함: {e}"
    return p.returncode, p.stdout.strip() or p.stderr.strip()


def cli_status(bin_path):
    """CliStatus 모양. 이메일·조직은 담지 않는다 (화면에 필요 없다)."""
    s = {"installed": False, "path": bin_path, "version": None, "logged_in": False, "auth_method": None,
         "subscription_type": None, "error": None}
    if not bin_path or not (os.path.exists(bin_path) or shutil.which(bin_path)):
        s["error"] = "claude CLI를 못 찾았다 — 설치하거나 RADAR_CLAUDE_BIN"
        return s
    code, out = _call(bin_path, "--version")
    if code != 0:
        s["error"] = f"claude --version 실패: {out[:200]}"
        return s
    s["installed"], s["version"] = True, out.splitlines()[0].strip() if out else None
    code, out = _call(bin_path, "auth", "status", "--json")
    try:
        data = json.loads(out) if code is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        s["error"] = f"claude auth status를 못 읽었다: {(out or '')[:200]}"
        return s
    s["logged_in"] = data.get("loggedIn") is True
    s["auth_method"], s["subscription_type"] = data.get("authMethod"), data.get("subscriptionType")
    return s


def readiness(engine, cli, api_key):
    """(detect·revisit을 지금 돌릴 수 있나, 아니면 이유)."""
    if engine is None:
        return True, None
    if engine == "api":
        return (True, None) if api_key else (False, ".env에 ANTHROPIC_API_KEY가 없다")
    if not cli["installed"]:
        return False, cli["error"] or "claude CLI가 없다"
    if cli["error"]:
        return False, cli["error"]
    if not cli["logged_in"]:
        return False, "claude에 로그인하지 않았다 — 로그인 창을 연다"
    if cli["auth_method"] != SUBSCRIPTION:
        return False, f"구독(claude.ai) 로그인이 아니다 (authMethod={cli['auth_method']}) — 그대로 돌리면 API로 과금된다"
    return True, None


def login_command(bin_path):
    """사람이 직접 칠 명령 한 줄 (터미널을 못 열 때 화면이 보여 준다)."""
    return " ".join(shlex.quote(a) for a in [bin_path or "claude", *LOGIN_ARGS])


def launch_spec(platform, bin_path, tmp_dir):
    """OS 터미널에서 로그인을 여는 (argv, Popen 추가 인자). 못 열면 None. macOS는 tmp_dir에 .command 파일을 쓴다."""
    bin_path = bin_path or "claude"
    if platform == "win32":
        # 첫 글자가 따옴표면 cmd /k가 따옴표를 벗긴다 — call을 앞에 둬서 빈칸 든 경로도 그대로 간다. 창은 끝나도 남는다(/k)
        # 표준 입출력을 넘기지 않는다 — 넘기면 새 콘솔이 아니라 그쪽으로 나가 창이 비어 보인다
        return ["cmd.exe", "/k", "call", *_argv(bin_path), *LOGIN_ARGS], {"creationflags": CREATE_NEW_CONSOLE}
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    line = " ".join(shlex.quote(a) for a in [*_argv(bin_path), *LOGIN_ARGS])
    if platform == "darwin":
        path = os.path.join(tmp_dir, "radar-claude-login.command")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh\n" + "".join(f"unset {k}\n" for k in STRIP_ENV) + line +
                    "\necho\necho '로그인이 끝났으면 이 창을 닫고 앱으로 돌아간다.'\n")
        os.chmod(path, 0o700)
        return ["open", "-a", "Terminal", path], quiet
    term = shutil.which("x-terminal-emulator")
    return ([term, "-e", "sh", "-c", line + "; exec sh"], quiet) if term else None


def open_login(bin_path, *, platform=None, launcher=subprocess.Popen, tmp_dir=None):
    """LoginResult 모양. launcher는 테스트가 바꿔 끼운다 (실제 창을 열지 않게)."""
    platform = platform or sys.platform
    cmd = login_command(bin_path)
    spec = launch_spec(platform, bin_path, tmp_dir or tempfile.gettempdir())
    if spec is None:
        return {"opened": False, "command": cmd, "detail": "터미널을 못 찾았다 — 이 명령을 직접 친다"}
    argv, extra = spec
    try:
        launcher(argv, env=_env(), **extra)
    except OSError as e:
        return {"opened": False, "command": cmd, "detail": f"터미널을 못 열었다 ({e}) — 이 명령을 직접 친다"}
    return {"opened": True, "command": cmd, "detail": "터미널에서 로그인을 마치면 엔진 상태를 다시 본다"}

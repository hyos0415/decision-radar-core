"""스킬 줄 — `skill@v1` detect를 라이브 모드(웹 검색)로 한 번 돌리고 후보를 뽑는다.

같은 순간 짝의 스킬 그림자(HYO-166, comparison-protocol.md §7)와 합집합 파이프라인의 스킬 줄(HYO-167, union-pipeline.md §4)이
**같은 실행기 · 같은 후보 뽑기**를 쓴다 — 그래야 그림자 결과가 곧 「합쳤다면」의 자료가 된다.

  실행 루트  eval 러너의 「스킬만 복사한 빈 실행 루트」(eval/runner/run_case.prepare_runroot, 규율 6). 스킬은 tag(`skill@v1`)에서 꺼낸다 —
            작업 트리의 스킬은 그 뒤로 바뀌었고, 스킬 기간 routine이 돌린 것은 tag와 같은 ADR repo 배포본이다
  ADR       부르는 쪽이 준 사본(radar.adr_copy — git이 아니라 push할 원격이 없다). 대상 레포는 사본의 radar-targets.json이 절대 경로로 가리킨다
  원격 차단  RADAR_DRY_RUN=1(스킬 규칙) + `--disallowedTools` git push · gh + PATH 맨 앞의 가짜 gh + 가짜 GH_TOKEN
            + git push 주소 바꿔치기(GIT_CONFIG_*의 pushInsteadOf — 대상 레포에서 push해도 닿지 않는다)
  대상 레포  읽기만 — 경로마다 Edit · Write 금지 규칙(write_deny). 맥에서는 실제 작업 사본이라서 (Bash로 쓰는 길은 스킬 규칙이 radar.py뿐)
  과금      API 키를 지운 env — 구독 `claude -p`로만 (product/radar/engines.STRIP_ENV와 같은 칸)
  출력      <work>/transcript.jsonl · stderr.txt · report.json(run_report) · candidates.json · meta.json(성공 · 실패 · 시간 — 내용 없음)

`run_detect`는 예외를 던지지 않는다 — 실패는 meta의 error로 돌려준다 (그림자가 죽어도 부르는 쪽은 산다).
"""
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

import yaml

from .detect_feed import url_key
from .importance.scope import vtuple
from .importance.signals import norm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "eval", "runner"))
from run_case import extract_report, prepare_runroot, tool_uses  # noqa: E402  — eval 러너와 같은 격리 · 같은 run_report 읽기

SKILL_REF = "skill@v1"  # 비교 프로토콜 §7 — 시작일에 적은 tag
SKILL_PATH = ".claude/skills/jhs-decisions"
MODEL, EFFORT = "claude-sonnet-5-5", "medium"  # 제품 주 엔진과 같은 모델 · effort (§7)
TIMEOUT = 1800  # 라이브 웹 검색은 분 단위 — eval 러너의 1200초보다 넉넉히
STRIP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")  # 남아 있으면 Claude Code가 API 키로 과금한다 — 구독으로만
BLOCKED = "blocked://radar-skill-line/"
PUSH_PREFIXES = ("https://", "http://", "ssh://", "git@", "git://", "file://")
TOOLS = ("Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill", "WebSearch", "WebFetch")
DENY = ("Bash(git push:*)", "Bash(gh:*)")
PROMPT = ("/jhs-decisions detect\n\n지금 시각: {now}\n"
          "ADR 디렉터리와 대상 repo는 환경변수(RADAR_ADR_DIR, RADAR_TARGETS)에 있다. 라이브 모드이며 dry-run이다.\n")
_VERSION = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+){1,3}(?:[-.]?(?:rc|alpha|beta|a|b|dev|post)\.?\d*)?)(?![\w.])", re.I)
_REMOTE = re.compile(r"\bgit\b[^\n|;&]*\bpush\b|(?:^|[\s;&|(])gh\s+(?:pr|issue|api|release|repo)\b")


def export_skill(dst, ref=SKILL_REF, repo=REPO):
    """tag의 스킬 폴더를 dst에 꺼낸다 → 커밋 sha. git show로 파일마다 — OS마다 다른 tar·zip에 기대지 않는다."""
    def git(*a):
        p = subprocess.run(["git", "-C", repo, *a], capture_output=True)
        if p.returncode:
            raise RuntimeError(f"git {' '.join(a[:2])} → {p.stderr.decode('utf-8', 'replace').strip()[:200]}"
                               + (f" — {ref} tag가 없으면 git fetch --tags" if ref != "HEAD" else ""))
        return p.stdout
    sha = git("rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
    names = git("ls-tree", "-r", "--name-only", sha, "--", SKILL_PATH).decode("utf-8").splitlines()
    if not names:
        raise RuntimeError(f"{ref}에 {SKILL_PATH}가 없다")
    for name in names:
        p = os.path.join(dst, os.path.relpath(name, SKILL_PATH))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(git("show", f"{sha}:{name}"))
    return sha


def _shims(d):
    """PATH 맨 앞에 둘 가짜 gh — Git Bash(sh)와 cmd 둘 다."""
    os.makedirs(d, exist_ok=True)
    sh = os.path.join(d, "gh")
    with open(sh, "w", encoding="utf-8", newline="\n") as f:
        f.write('#!/bin/sh\necho "radar skill line: gh is blocked (shadow/dry-run)" >&2\nexit 1\n')
    os.chmod(sh, 0o755)
    with open(os.path.join(d, "gh.cmd"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("@echo radar skill line: gh is blocked (shadow/dry-run) 1>&2\n@exit /b 1\n")
    return d


def child_env(work, adr_dir, targets, base=None):
    """스킬 실행 env — 물려받은 RADAR_*·API 키를 지우고 라이브 · dry-run · 원격 차단으로."""
    env = {k: v for k, v in (base if base is not None else os.environ).items() if not k.startswith("RADAR_") and k not in STRIP_ENV}
    tmp = os.path.join(work, "tmp")
    os.makedirs(tmp, exist_ok=True)
    env.update({"RADAR_ADR_DIR": adr_dir, "RADAR_TARGETS": targets, "RADAR_DRY_RUN": "1",  # RADAR_SNAPSHOT_DIR 없음 = 라이브 모드
                "PATH": os.pathsep.join([_shims(os.path.join(work, "shims")), os.path.dirname(sys.executable), env.get("PATH", "")]),
                "GH_TOKEN": "radar-skill-line-blocked", "GITHUB_TOKEN": "radar-skill-line-blocked", "GH_PROMPT_DISABLED": "1",
                "GIT_TERMINAL_PROMPT": "0", "PYTHONIOENCODING": "utf-8", "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp,
                "GIT_CONFIG_COUNT": str(len(PUSH_PREFIXES))})
    for i, pre in enumerate(PUSH_PREFIXES):
        env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"] = f"url.{BLOCKED}.pushInsteadOf", pre
    return env


def write_deny(paths):
    """대상 레포는 읽기만 — 경로마다 Edit · Write 금지 규칙 (리뷰 11/4: 맥의 실제 작업 사본을 --add-dir로 연다).
    Claude Code 권한 규칙의 절대 경로는 `//`로 시작한다 (`Edit(//Users/me/repo/**)`). Windows 드라이브는 `/c/…` 꼴로 적는다."""
    out = []
    for p in paths:
        a = os.path.abspath(p).replace("\\", "/").rstrip("/")
        if re.match(r"^[A-Za-z]:/", a):
            a = "/" + a[0].lower() + a[2:]
        out += [f"Edit(/{a}/**)", f"Write(/{a}/**)"]
    return out


def command(bin_, prompt, add_dirs, model=MODEL, effort=EFFORT, read_only=()):
    """read_only: --add-dir로 열되 쓰지 못하게 할 경로 (대상 레포)."""
    b = [sys.executable, bin_] if bin_.endswith(".py") else [bin_]  # .py = 테스트용 가짜 CLI
    return [*b, "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", model, "--effort", effort,
            "--setting-sources", "project", "--no-session-persistence", "--permission-mode", "dontAsk",
            *[x for d in add_dirs for x in ("--add-dir", d)],
            "--allowedTools", *TOOLS, "--disallowedTools", *DENY, *write_deny(read_only)]


def _kill_tree(proc):  # product/radar/engines._kill_tree와 같다 — 이 모듈은 수집 PC 사슬에서도 돌아 product를 끌어오지 않는다
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _adr_files(adr_dir):
    return {f for f in os.listdir(adr_dir) if re.match(r"^\d{4}-.*\.md$", f)}


def remote_attempts(uses):
    """원격에 닿으려 한 Bash 호출 수 (git push · gh pr/issue/api …) — 막혔어도 시도는 센다."""
    return sum(1 for u in uses if u["name"] == "Bash" and _REMOTE.search(u["input"].get("command") or ""))


def run_detect(work, *, adr_dir, targets, now, claude_bin, ref=SKILL_REF, model=MODEL, effort=EFFORT, timeout=TIMEOUT, base_env=None):
    """스킬 detect 한 번 → meta. work 안에 runroot · 출력 파일을 둔다. 예외를 던지지 않는다."""
    meta = {"ok": False, "error": None, "skill_ref": ref, "skill_commit": None, "model": model, "effort": effort, "now": now,
            "started_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "ended_at": None, "wall_s": None,
            "returncode": None, "num_turns": None, "tool_calls": None, "remote_attempts": None, "cost_usd_equiv": None, "billing": "subscription"}
    t0 = time.time()
    try:
        os.makedirs(work, exist_ok=True)
        src = os.path.join(work, "skill-src")
        shutil.rmtree(src, ignore_errors=True)
        meta["skill_commit"] = export_skill(src, ref)
        runroot = prepare_runroot(work, src=src)
        shutil.rmtree(src, ignore_errors=True)
        before = _adr_files(adr_dir)
        repos = []
        try:
            repos = [t["path"] for t in json.load(open(targets, encoding="utf-8")) if os.path.isdir(t.get("path") or "")]
        except (OSError, ValueError, TypeError, KeyError):
            pass
        dirs = [os.path.dirname(os.path.dirname(adr_dir))] + repos   # ADR 사본(초안을 여기 쓴다) + 대상 레포(읽기만)
        proc = subprocess.Popen(command(claude_bin, PROMPT.format(now=now), dirs, model, effort, read_only=repos), cwd=runroot,
                                env=child_env(work, adr_dir, targets, base_env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, **({} if sys.platform == "win32" else {"start_new_session": True}))
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, err = proc.communicate()
            meta["error"] = f"timeout {timeout}s"
        meta["returncode"] = proc.returncode
        out, err = out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        with open(os.path.join(work, "transcript.jsonl"), "w", encoding="utf-8") as f:
            f.write(out)
        if err:
            with open(os.path.join(work, "stderr.txt"), "w", encoding="utf-8") as f:
                f.write(err)
        events = [json.loads(x) for x in out.splitlines() if x.strip().startswith("{")]
        uses = tool_uses(events)
        final = next((e for e in reversed(events) if e.get("type") == "result"), {})
        meta.update(num_turns=final.get("num_turns"), tool_calls=len(uses), remote_attempts=remote_attempts(uses),
                    cost_usd_equiv=final.get("total_cost_usd"))
        report = extract_report(final.get("result") or "") or extract_report(out)
        drafts = sorted(_adr_files(adr_dir) - before)
        json.dump({"report": report}, open(os.path.join(work, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(extract_candidates(report, adr_dir, drafts), open(os.path.join(work, "candidates.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        if meta["error"] is None:
            if proc.returncode or final.get("is_error"):
                meta["error"] = f"claude 종료 {proc.returncode} · {final.get('subtype')} {err.strip()[-200:]}".strip()
            elif report is None:
                meta["error"] = "run_report가 없다"
        meta["ok"] = meta["error"] is None
    except Exception as e:  # 그림자 · 스킬 줄이 죽어도 부르는 쪽은 산다
        meta["error"] = f"{type(e).__name__}: {str(e)[:300]}"
    meta["ended_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    meta["wall_s"] = round(time.time() - t0, 1)
    try:
        json.dump(meta, open(os.path.join(work, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError:
        pass
    return meta


# ── 후보 뽑기 (union-pipeline.md §4) — 스킬 초안의 근거 링크 + 패키지 · 버전 ─────────────
# transcript에서 연 URL(WebSearch · WebFetch)은 넣지 않는다 — §6 E5(봉인 해제 11/22 뒤 결정)

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)
_H1 = re.compile(r"^#\s+(.+)$", re.M)


def version_of(*texts):
    """제목 · use에서 처음 보이는 버전 (`v0.32.0` → `0.32.0`). 없으면 None."""
    for t in texts:
        m = _VERSION.search(t or "")
        if m:
            return m.group(1)
    return None


def major(version):
    """같은 항목 기준의 「주 버전」 — 1 이상이면 첫 자리, 0.x면 `0.<둘째>` (0.x는 둘째 자리가 깨지는 릴리스다). 모르면 None."""
    v = vtuple(version)
    if not v:
        return None
    return str(v[0]) if v[0] or len(v) < 2 else f"0.{v[1]}"


def item_key(package=None, version=None, url=None):
    """같은 항목 = 정규화 패키지 이름 + 릴리스 주 버전, 패키지가 없으면 정규화 URL (재생 · 그림자 · 합집합이 같은 기준, §7)."""
    if package and major(version):
        return f"pkg:{norm(package)}@{major(version)}"
    return f"url:{url_key(url).lower()}" if url else None


def extract_candidates(report, adr_dir, drafts):
    """→ {"drafts": [...], "skipped_existing": [...]} — drafts는 이번 실행이 쓴 초안 파일마다 하나 (run_report가 없어도 파일로 뽑는다)."""
    by_tech = {norm(c.get("technology") or ""): c for c in (report or {}).get("candidates") or [] if isinstance(c, dict)}
    out = []
    for name in drafts:
        text = open(os.path.join(adr_dir, name), encoding="utf-8").read()
        m = _FM.match(text)
        try:
            fm = (yaml.safe_load(m.group(1)) if m else None) or {}
        except yaml.YAMLError:
            fm = {}
        h1 = _H1.search(m.group(2) if m else text)
        title = h1.group(1).strip() if h1 else None
        tech = str(fm.get("technology") or "")
        rc = by_tech.get(norm(tech)) or {}
        ev = [e for e in fm.get("evidence") or [] if isinstance(e, dict) and e.get("url")]
        urls = list(dict.fromkeys([e["url"] for e in ev] + [u for u in rc.get("source_urls") or [] if isinstance(u, str)]))
        out.append({"adr": name[:4], "file": name, "title": title, "technology": tech, "use": fm.get("use"),
                    "version": version_of(title, fm.get("use")), "dependency": rc.get("dependency"),
                    "affected": fm.get("affected") or rc.get("affected") or [],
                    "evidence": [{"url": e["url"], "source_type": e.get("source_type")} for e in ev], "urls": urls})
    skipped = [s for s in (report or {}).get("skipped_existing") or [] if isinstance(s, dict)]
    return {"drafts": out, "skipped_existing": skipped}

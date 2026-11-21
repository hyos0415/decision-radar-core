"""승인한 의존성 릴리스를 대상 프로젝트의 **격리된 사본**에서 올려 보고 기존 테스트로 확인한다 (HYO-239 — 뒷단 1).

레이더는 「이 릴리스를 볼 만하다」까지만 알린다. 사람이 승인한 뒤 「올려도 우리 코드가 안 깨지나」는 사람 손으로 남아 있었다 —
여기서는 그 한 걸음을 스크립트로 한다. 모델을 부르지 않는다.

  python -m pipeline.tryout --adr ../adr/docs/adr/0017-fastapi.md --repo decision-radar --component desktop --checks ../adr/radar-checks.json

  1. 사본   대상 repo를 임시 폴더로 clone하고 원격 기본 브랜치(대상의 `origin/<기본>`)에 맞춘다. 대상의 작업 폴더 · 브랜치는 건드리지 않는다
  2. 기준   지금 고정된 버전 그대로 환경을 만들고(uv) 그 component의 검사 스크립트를 돈다 — 원래 깨져 있던 것을 가린다
  3. 올림   manifest의 `이름==버전` 줄을 릴리스 버전으로 고친다 (못 찾으면 멈춘다 — 줄을 지어내지 않는다)
  4. 다시   같은 환경에 다시 설치하고 같은 스크립트를 돈다
  5. 결과   result.json · report.md — 통과 / 깨짐 / 확인 불가와 그 이유

원칙 (HYO-116에서 가져온다):
- **결정은 사람** — ADR을 고치지 않는다. 결과는 근거일 뿐이다
- **대상에 쓰지 않는다** — 사본에서만 돈다. 브랜치 push · PR은 `--export`를 줬고 판정이 `pass`일 때만 한다 (draft)
- **통과는 증거가 아니다** (규율 3) — 검사 스크립트가 그 패키지를 실제로 올렸는지(실행 뒤 `sys.modules`)를 같이 본다.
  하나도 안 올렸으면 「통과」가 아니라 「이 검사로는 모른다」(pass_not_exercised)다. 자식 프로세스에서만 올리는 스크립트는 못 본다 — 그렇게 적는다

검사 설정 (`--checks`, 대상 목록 옆 `radar-checks.json`) — repo · component마다 무엇을 설치하고 무엇을 도는지:
  {"<repo>": {"python": "3.12", "components": {"<component>": {
      "manifests": ["product/requirements.txt"],                       # 고정 줄을 찾을 파일
      "install": ["requirements.txt", "product/requirements.txt"],     # uv pip install -r … (repo 루트 기준)
      "run": [{"script": "desktop/backend/test_backend.py", "args": []}]}}}}
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

VERDICTS = {
    "pass": "통과 — 올려도 기존 검사가 깨지지 않고, 검사가 이 패키지를 실제로 올렸다",
    "pass_not_exercised": "모름 — 검사는 통과했지만 이 패키지를 올린 검사가 없다 (이 검사로는 영향을 알 수 없다)",
    "pass_partial": "일부만 — 깨진 검사는 없지만 올리기 전부터 실패한 검사가 있다 (그 검사로는 영향을 알 수 없다)",
    "fail": "깨짐 — 기준에서는 통과하던 검사가 올린 뒤 실패한다",
    "install_failed": "확인 불가 — 올린 버전으로 설치가 안 된다 (다른 고정 버전과 충돌할 수 있다)",
    "not_applied": "확인 불가 — 설치는 됐지만 설치된 버전이 올리려던 버전이 아니다",
    "baseline_failed": "확인 불가 — 올리기 전부터 설치 · 검사가 안 된다 (기준이 없다)",
    "no_pin": "확인 불가 — manifest에서 이 패키지의 고정 줄(`이름==버전`)을 못 찾았다",
    "no_checks": "확인 불가 — 이 component에 돌릴 검사가 설정돼 있지 않다",
    "already": "할 일 없음 — 이미 그 버전이다",
}

PIN = re.compile(r"^(?P<lead>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-]*)(?P<extras>\[[^\]]*\])?\s*==\s*(?P<ver>[^\s#;]+)(?P<rest>.*)$")
_VER = re.compile(r"(?<![\w.])v?(\d+(?:\.\d+)+(?:[.\-]?(?:a|b|rc|alpha|beta|dev|post)[.\-]?\d*)?)(?![\w.])", re.I)

# 검사 스크립트를 감싸 돈다 — 끝난 뒤 그 패키지가 올라와 있었는지(sys.modules)를 적는다. 스크립트의 종료 코드는 그대로 돌려준다
PROBE = '''import json, runpy, sys
out, names, script = sys.argv[1], [n for n in sys.argv[2].split(",") if n], sys.argv[3]
sys.argv = sys.argv[3:]
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(script)))
code = 0
try:
    runpy.run_path(script, run_name="__main__")
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
finally:
    json.dump({"loaded": {n: any(m == n or m.startswith(n + ".") for m in sys.modules) for n in names}}, open(out, "w"))
sys.exit(code)
'''


def norm(name):
    return re.sub(r"[-_.]+", "-", (name or "").strip().lower())


def import_name(package):
    return norm(package).replace("-", "_")


def read_adr(path):
    """결정 기록 → {id, technology, use, title, status, affected}. frontmatter만 읽는다."""
    text = Path(path).read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SystemExit(f"{path}: frontmatter가 없다")
    fm = yaml.safe_load(parts[1]) or {}
    return {"id": Path(path).name.split("-", 1)[0], "technology": fm.get("technology"), "use": fm.get("use") or "", "title": fm.get("title") or "",
            "status": fm.get("status"), "affected": fm.get("affected") or [], "path": str(path)}


def release_version(adr):
    """결정 기록의 use(「의존성 릴리스 0.162.6 대응」 · 「… langchain-core==1.8.0 대응」) → 버전 | None. 못 읽으면 None — `--to`로 넘긴다."""
    found = _VER.findall(adr.get("use") or "") or _VER.findall(adr.get("title") or "")
    return found[-1] if found else None


def bump_pin(text, package, version):
    """manifest 글에서 `package==X` 줄을 `package==version`으로 → (고친 글, 옛 버전 | None). 줄의 나머지(주석 · 마커)는 그대로 둔다."""
    old, out = None, []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        m = PIN.match(body)
        if m and norm(m.group("name")) == norm(package) and old is None:
            old = m.group("ver")
            body = f"{m.group('lead')}{m.group('name')}{m.group('extras') or ''}=={version}{m.group('rest')}"
            line = body + line[len(line.rstrip("\r\n")):]
        out.append(line)
    return "".join(out), old


def sh(cmd, cwd, log, timeout=1800, env=None):
    """명령 하나 → {cmd, exit, seconds, log}. 출력은 log 파일에 덧붙인다 (시간 초과 = exit 124)."""
    t0 = time.perf_counter()
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n$ {' '.join(map(str, cmd))}\n")
        f.flush()
        try:
            code = subprocess.run([str(c) for c in cmd], cwd=cwd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout, env=env).returncode
        except subprocess.TimeoutExpired:
            f.write(f"\n[시간 초과 {timeout}s]\n")
            code = 124
        except OSError as e:
            f.write(f"\n[실행 실패 {e}]\n")
            code = 127
    return {"cmd": [str(c) for c in cmd], "exit": code, "seconds": round(time.perf_counter() - t0, 1), "log": str(log)}


def copy_repo(src, dest, log):
    """대상 repo를 dest로 clone하고 대상의 원격 기본 브랜치(origin/HEAD가 가리키는 것, 없으면 main)에 맞춘다 → (커밋, 어디에 맞췄나).
    대상에는 아무것도 쓰지 않는다 — clone은 읽기만 하고, 원격 추적 ref는 사본 쪽에서 가져온다(fetch)."""
    r = sh(["git", "clone", "--quiet", "--no-hardlinks", src, dest], None, log)
    if r["exit"] != 0:
        raise SystemExit(f"사본을 못 만들었다: {src} (로그 {log})")
    head = subprocess.run(["git", "-C", src, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], capture_output=True, text=True).stdout.strip() or "origin/main"
    based = "작업 폴더의 HEAD"
    if sh(["git", "-C", dest, "fetch", "--quiet", src, f"refs/remotes/{head}"], None, log)["exit"] == 0:
        sh(["git", "-C", dest, "checkout", "--quiet", "--detach", "FETCH_HEAD"], None, log)
        based = f"대상의 {head} (마지막 fetch 기준)"
    commit = subprocess.run(["git", "-C", dest, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    return commit, based


class Env:
    """uv로 만드는 임시 가상환경. 테스트는 install · python을 바꿔 끼운다."""

    def __init__(self, root, python="3.12"):
        self.root, self.version = Path(root), python
        self.python = self.root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    def create(self, log):
        return sh(["uv", "venv", "--quiet", "--python", self.version, self.root], None, log)

    def install(self, repo, files, log):
        cmd = ["uv", "pip", "install", "--quiet", "--python", self.python]
        for f in files:
            cmd += ["-r", f]
        return sh(cmd, repo, log)

    def version_of(self, package):
        r = subprocess.run([str(self.python), "-c", f"import importlib.metadata as m; print(m.version({package!r}))"], capture_output=True, text=True)
        return r.stdout.strip() or None


CHECK_ENV_DROP = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY", "ELECTRON_RUN_AS_NODE")


def check_env(base=None):
    """검사 스크립트에 넘길 환경 — 부른 쪽의 설정이 새지 않게 `RADAR_*` · API 키를 뺀다.
    실사용 앱 백엔드가 실험을 돌리면 앱의 `RADAR_APP_MODE=real`이 검사로 새어 test_backend가 실사용 모드로 열리다 실패했다 (HYO-116,
    2026-11-21 — 핵심 검사가 「원래 실패」가 되고 판정은 남은 검사 하나로 「통과」가 났다). 검사는 키 없이 돈다 (유료 API를 부르지 않게)."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not k.startswith("RADAR_") and k not in CHECK_ENV_DROP}
    return {**env, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def run_checks(env, repo, runs, names, out, tag, timeout=1800):
    """검사 스크립트를 차례로 돈다 → [{script, exit, seconds, loaded, log}]. 환경은 check_env() — 부른 쪽 설정이 새지 않게."""
    probe = Path(out) / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    rows = []
    for i, r in enumerate(runs):
        seen = Path(out) / f"{tag}-{i}.loaded.json"
        log = Path(out) / f"{tag}-{i}.log"
        res = sh([env.python, probe, seen, ",".join(names), r["script"], *(r.get("args") or [])], repo, log, timeout, env=check_env())
        loaded = {}
        if seen.exists():
            try:
                loaded = json.loads(seen.read_text(encoding="utf-8")).get("loaded") or {}
            except ValueError:
                loaded = {}
        rows.append({"script": r["script"], "args": r.get("args") or [], "exit": res["exit"], "seconds": res["seconds"], "loaded": loaded, "log": str(log)})
    return rows


def judge(base, after, package):
    """기준 · 올린 뒤 검사 결과 → (verdict, 스크립트별 줄). 기준에서 실패하던 스크립트는 「원래 실패」로 따로 센다 — 올린 탓으로 치지 않는다."""
    name, rows, broke, exercised = import_name(package), [], False, False
    for b, a in zip(base, after):
        if b["exit"] != 0:
            state = "원래 실패"
        elif a["exit"] != 0:
            state, broke = "깨짐", True
        else:
            state = "통과"
            exercised = exercised or bool(a["loaded"].get(name))
        rows.append({"script": a["script"], "base_exit": b["exit"], "after_exit": a["exit"], "state": state, "loaded": bool(a["loaded"].get(name))})
    if broke:
        return "fail", rows
    if not any(r["state"] == "통과" for r in rows):
        return "baseline_failed", rows
    if not exercised:                                  # 통과한 검사가 이 패키지를 안 올렸다 — 「모름」이 먼저다
        return "pass_not_exercised", rows
    if any(r["state"] == "원래 실패" for r in rows):   # 원래 실패한 검사가 있으면 남은 검사로 「통과」라 하지 않는다 (규율 3 — HYO-116, 내보내지 않는다)
        return "pass_partial", rows
    return "pass", rows


def bump_all(repo, manifests, bumps):
    """manifest들에서 bumps [(패키지, 버전)]의 고정 줄을 고친 글을 만든다 (아직 쓰지 않는다) → ({manifest: 고친 글}, [{package, manifest, from, to}])."""
    texts, pins = {}, []
    for m in manifests:
        if not (Path(repo) / m).exists():
            continue
        text = (Path(repo) / m).read_text(encoding="utf-8")
        for package, version in bumps:
            text, old = bump_pin(text, package, version)
            if old:
                pins.append({"package": package, "manifest": m, "from": old, "to": version})
        texts[m] = text
    return texts, pins


def _tryout(adr, repo_name, repo_path, component, check, package, version, out, env=None, keep=False, extra=(), export=False):
    """한 건을 끝까지 → result dict. out 폴더에 result.json · report.md · 로그를 쓴다. check = 그 component의 검사 설정.
    extra = 함께 올릴 [(패키지, 버전)] — 혼자서는 설치가 안 되는 릴리스(langchain 1.5.0은 langchain-core 1.8.0 이상이 필요하다)를 같이 올려 본다."""
    out = Path(out).resolve()          # 검사는 사본 폴더에서 돈다 — 상대 경로면 probe를 못 찾는다
    out.mkdir(parents=True, exist_ok=True)
    log = out / "steps.log"
    res = {"adr": adr["id"], "technology": adr["technology"], "package": package, "to": version, "repo": repo_name, "component": component,
           "with": [{"package": p, "to": v} for p, v in extra],
           "at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "verdict": None, "steps": []}
    work = Path(tempfile.mkdtemp(prefix="radar-tryout-"))
    try:
        repo = work / "repo"
        res["commit"], res["based_on"] = copy_repo(repo_path, repo, log)
        manifests = check.get("manifests") or []
        texts, pins = bump_all(repo, manifests, [(package, version), *extra])
        res["pins"] = pins
        mine = [p for p in pins if norm(p["package"]) == norm(package)]
        missing = [p for p, _ in extra if not any(norm(x["package"]) == norm(p) for x in pins)]
        if not mine or missing:        # 주 패키지든 함께 올릴 패키지든 고정 줄이 없으면 멈춘다 — 일부만 올린 결과를 내지 않는다
            res["no_pin_for"] = ([package] if not mine else []) + missing
            return finish(res, "no_pin", out)
        if all(p["from"] == p["to"] for p in pins):
            return finish(res, "already", out)
        if not check.get("run"):
            return finish(res, "no_checks", out)
        env = env or Env(work / "venv", check.get("python") or "3.12")
        names = [import_name(package)]
        r = env.create(log)
        res["steps"].append({"step": "환경 만들기", **r})
        r1 = env.install(repo, check.get("install") or manifests, log) if r["exit"] == 0 else r
        res["steps"].append({"step": "기준 설치", **r1})
        if r["exit"] != 0 or r1["exit"] != 0:
            return finish(res, "baseline_failed", out)
        res["installed_before"] = env.version_of(package)
        base = run_checks(env, repo, check["run"], names, out, "base")
        for m, text in texts.items():
            (repo / m).write_text(text, encoding="utf-8", newline="")
        res["diff"] = subprocess.run(["git", "-C", str(repo), "diff", "--no-color"], capture_output=True, text=True).stdout
        r2 = env.install(repo, check.get("install") or manifests, log)
        res["steps"].append({"step": "올린 뒤 설치", **r2})
        if r2["exit"] != 0:
            res["install_error"] = tail(log, 25)
            res["checks"] = [{"script": b["script"], "base_exit": b["exit"], "after_exit": None, "state": "안 돌림", "loaded": False} for b in base]
            return finish(res, "install_failed", out)
        res["installed_after"] = env.version_of(package)
        if norm(res["installed_after"] or "") != norm(version):
            return finish(res, "not_applied", out)
        after = run_checks(env, repo, check["run"], names, out, "after")
        res["verdict"], res["checks"] = judge(base, after, package)
        res["seconds"] = {"base": sum(b["seconds"] for b in base), "after": sum(a["seconds"] for a in after)}
        if export and may_export(res["verdict"]):
            res["export"] = export_pr(res, adr, repo_path, repo, texts, log)
        return finish(res, res["verdict"], out)
    finally:
        if keep:
            res["work"] = str(work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def tryout(adr, repo_name, repo_path, component, check, package, version, out, env=None, keep=False, extra=(), export=False):
    """`_tryout` + 내보내기 표시 — export를 켰는데 통과가 아니면 「내보내지 않음」을 결과에 적는다."""
    res = _tryout(adr, repo_name, repo_path, component, check, package, version, out, env=env, keep=keep, extra=extra, export=export)
    if export and "export" not in res:
        res["export"] = {"branch": None, "pr_url": None, "skipped_reason": f"내보내지 않음: {res['verdict']}"}
        finish(res, res["verdict"], out)
    return res


GH_CREATE_FLAGS = ["--draft"]       # PR은 항상 draft — merge는 레포 주인의 결정


def may_export(verdict):
    """정확히 pass일 때만 — pass_not_exercised(이 검사로는 모름)도 내보내지 않는다."""
    return verdict == "pass"


def is_default_branch(branch, base):
    return branch == base or branch.startswith("refs/") or base in ("", "HEAD")


def files_to_add(texts):
    """커밋에 넣는 파일 = 고정 줄을 고친 manifest뿐 (검사가 만든 파일 · 환경은 넣지 않는다)."""
    return sorted(texts)


def open_prs(repo, branch):
    r = subprocess.run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url"], cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        return json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except ValueError:
        return []


def remote_has_branch(repo, branch):
    code, heads, _ = _git(["-C", str(repo), "ls-remote", "--heads", "origin", branch])
    return code == 0 and bool(heads)


def _git(args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def default_branch(src):
    """대상의 원격 기본 브랜치 이름 (origin/HEAD가 가리키는 것, 없으면 main)."""
    code, out, _ = _git(["-C", str(src), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
    return out.split("/", 1)[1] if code == 0 and "/" in out else "main"


def export_branch(package, version):
    return "radar/tryout/" + re.sub(r"[^A-Za-z0-9._\-]+", "-", f"{norm(package)}-{version}").strip("-.")


def pr_body(res, adr):
    pins = "\n".join(f"- `{p['manifest']}`: {p['package']} {p['from']} → {p['to']}" for p in res.get("pins") or [])
    rows = "\n".join(f"- `{c['script']}`: 기준 {'통과' if c['base_exit'] == 0 else '실패'} → 올린 뒤 "
                     f"{'통과' if c['after_exit'] == 0 else '실패'} ({c['state']}, 이 패키지를 올렸나: {'예' if c['loaded'] else '아니오'})"
                     for c in res.get("checks") or [])
    return (f"레이더의 올려 보기(`pipeline/tryout.py`)가 격리된 사본에서 올려 보고 기존 검사를 돌린 결과, 통과해서 올리는 PR입니다.\n\n"
            f"- 결정 기록: ADR {adr['id']} (`{adr.get('path', '')}`)\n- 대상: {res['repo']} · {res['component']}\n"
            f"- 버전: {res['package']} {res.get('installed_before') or '?'} → {res['to']}\n\n"
            f"고정 줄 변경:\n{pins}\n\n돌린 검사:\n{rows}\n\n"
            f"판정: `{res['verdict']}` — {VERDICTS[res['verdict']]}\n\n"
            "기존 검사가 덮지 않는 동작은 이 결과로 알 수 없습니다. 이 PR은 draft이며, merge 여부는 레포 주인이 정합니다.\n")


def export_pr(res, adr, src, repo, texts, log):
    """통과한 올려 보기를 대상의 브랜치 push + draft PR로 낸다 → {branch, pr_url, skipped_reason}.
    사본(repo)은 대상 작업 폴더를 clone한 git 체크아웃이고 origin/<기본>에 맞춰져 있어 새 clone이 필요 없다 —
    다만 사본의 origin은 대상 작업 폴더이므로 대상의 진짜 origin URL로 바꿔서 push한다. 기본 브랜치에는 절대 push하지 않는다."""
    base = default_branch(src)
    branch = export_branch(res["package"], res["to"])
    if is_default_branch(branch, base):
        raise SystemExit(f"내보내기 거부: 브랜치 {branch!r}가 기본 브랜치 {base!r}와 같다")
    code, url, _ = _git(["-C", str(src), "remote", "get-url", "origin"])
    if code != 0 or not url:
        raise SystemExit(f"내보내기 거부: 대상 repo에 origin이 없다 ({src})")
    if not Path(url).is_absolute() and (Path(src) / url).exists():
        url = str((Path(src) / url).resolve())
    out = {"branch": branch, "pr_url": None, "skipped_reason": None}
    _git(["-C", str(repo), "remote", "set-url", "origin", url])
    # 멱등 — 열린 PR이나 같은 이름 브랜치가 이미 있으면 다시 push하지 않는다
    prs = open_prs(repo, branch)
    if prs:
        out.update(pr_url=prs[0].get("url"), skipped_reason="이미 열린 PR이 있다 — 다시 내보내지 않음")
        return out
    if remote_has_branch(repo, branch):
        out["skipped_reason"] = "같은 이름의 브랜치가 이미 있다 — 다시 push하지 않음 (PR은 직접 확인)"
        return out
    changed = files_to_add(texts)
    ident = []
    if not _git(["-C", str(repo), "config", "user.name"])[1]:
        ident = ["-c", "user.name=decision-radar", "-c", "user.email=decision-radar@users.noreply.github.com"]
    steps = [["checkout", "-q", "-b", branch], ["add", "--", *changed], [*ident, "commit", "-q", "-m", f"{res['package']} {res['to']} 올려 보기 통과 (ADR {adr['id']})"]]
    for st in steps:
        c, _, e = _git(["-C", str(repo), *st])
        if c != 0:
            out["skipped_reason"] = f"내보내기 실패: git {st[0]} — {e[:200]}"
            return out
    c, _, e = _git(["-C", str(repo), "push", "-q", "origin", f"{branch}:refs/heads/{branch}"])
    if c != 0:
        out["skipped_reason"] = f"내보내기 실패: push — {e[:200]}"
        return out
    title = f"{res['package']} {(res.get('pins') or [{}])[0].get('from', '?')} → {res['to']} 올려 보기 통과 (ADR {adr['id']})"
    r = subprocess.run(["gh", "pr", "create", *GH_CREATE_FLAGS, "--base", base, "--head", branch, "--title", title, "--body", pr_body(res, adr)],
                       cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        out["skipped_reason"] = f"브랜치는 push했지만 PR 생성 실패: {(r.stderr or '')[:200]}"
        return out
    out["pr_url"] = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else None
    return out


def tail(path, n):
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def finish(res, verdict, out):
    res["verdict"], res["verdict_text"] = verdict, VERDICTS[verdict]
    (Path(out) / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    (Path(out) / "report.md").write_text(report(res), encoding="utf-8", newline="\n")
    return res


def report(res):
    """사람이 읽는 결과 한 장."""
    lines = [f"# 올려 보기: {res['package']} → {res['to']} ({res['repo']} · {res['component']})", "",
             f"**결과: {res['verdict_text']}**", "",
             f"- 결정 기록 {res['adr']} ({res['technology']}) · 실행 {res['at']}",
             f"- 사본 기준: {res.get('based_on', '—')} `{res.get('commit', '—')}` — 대상 repo에는 아무것도 쓰지 않았다"]
    for p in res.get("pins") or []:
        lines.append(f"- 고정 줄: `{p['manifest']}` {p['package']} {p['from']} → {p['to']}")
    if res.get("with"):
        lines.append("- 함께 올림: " + " · ".join(f"{w['package']} {w['to']}" for w in res["with"]) + " — 이 결과는 **같이 올렸을 때**의 것이다")
    if res.get("no_pin_for"):
        lines.append("- 고정 줄을 못 찾은 패키지: " + ", ".join(res["no_pin_for"]))
    if res.get("installed_before") or res.get("installed_after"):
        lines.append(f"- 설치된 버전: {res.get('installed_before')} → {res.get('installed_after')}")
    if res.get("checks"):
        lines += ["", "| 검사 | 기준 | 올린 뒤 | 판정 | 이 패키지를 올렸나 |", "|---|---|---|---|---|"]
        for c in res["checks"]:
            after = "—" if c["after_exit"] is None else ("통과" if c["after_exit"] == 0 else f"실패({c['after_exit']})")
            lines.append(f"| `{c['script']}` | {'통과' if c['base_exit'] == 0 else '실패(' + str(c['base_exit']) + ')'} | {after} | {c['state']} | {'예' if c['loaded'] else '아니오'} |")
        lines += ["", "「이 패키지를 올렸나」는 검사 프로세스 안에서만 본다 — 자식 프로세스에서만 쓰는 검사는 「아니오」로 나온다."]
    if res.get("install_error"):
        lines += ["", "설치 오류 (끝 25줄):", "", "```", res["install_error"], "```"]
    lines += ["", "이 결과는 근거다 — 채택 여부는 사람이 정한다. 기존 검사가 덮지 않는 동작은 이 결과로 알 수 없다.", ""]
    return "\n".join(lines)


def load_targets(path):
    """radar-targets.json → {repo: 절대 경로}."""
    base = Path(path).resolve().parent
    return {t["repo"]: str((base / t["path"]).resolve()) for t in json.loads(Path(path).read_text(encoding="utf-8"))}


def choose_target(adr, targets, checks, repo=None, component=None):
    """결정 기록의 affected 가운데 대상 목록과 검사 설정에 둘 다 있는 첫 곳 → (repo, component, 검사 설정).
    없으면 ValueError(이유) — 명령줄과 실험 실행기(pipeline/experiment.py)가 같이 쓴다."""
    cands = [(x["repo"], x["component"]) for x in adr["affected"] if (not repo or x["repo"] == repo) and (not component or x["component"] == component)]
    if repo and component and (repo, component) not in cands:
        cands = [(repo, component)]
    pick = next(((r, c) for r, c in cands if r in targets and c in ((checks.get(r) or {}).get("components") or {})), None)
    if not pick:
        raise ValueError(f"돌릴 곳이 없다 — 결정 기록의 affected {cands} 가운데 대상 목록과 검사 설정에 둘 다 있는 곳이 없다")
    r, c = pick
    return r, c, {"python": checks[r].get("python"), **checks[r]["components"][c]}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="pipeline.tryout", description="승인한 의존성 릴리스를 격리된 사본에서 올려 보고 기존 검사로 확인한다 (HYO-239)")
    ap.add_argument("--adr", required=True, help="결정 기록 파일 (의존성 릴리스 대응)")
    ap.add_argument("--targets", help="대상 목록 (기본: 결정 기록 repo의 radar-targets.json)")
    ap.add_argument("--checks", help="검사 설정 (기본: 대상 목록 옆 radar-checks.json)")
    ap.add_argument("--repo", help="대상 repo 이름 (기본: 결정 기록의 affected 가운데 검사 설정이 있는 첫 곳)")
    ap.add_argument("--component")
    ap.add_argument("--package", help="manifest의 패키지 이름 (기본: 결정 기록의 technology)")
    ap.add_argument("--to", help="올릴 버전 (기본: 결정 기록의 use에서 읽는다)")
    ap.add_argument("--with", dest="extra", action="append", default=[], metavar="패키지==버전",
                    help="함께 올릴 패키지 (여러 번) — 혼자서는 설치가 안 될 때. 결과에 같이 올렸다고 적힌다")
    ap.add_argument("--out", help="결과 폴더 (기본: pipeline/out/tryout/<기록>-<패키지>-<버전>-<repo>-<component>)")
    ap.add_argument("--export", action="store_true", help="통과(pass)하면 대상 repo에 radar/tryout/<패키지>-<버전> 브랜치를 push하고 draft PR을 연다 (기본: 안 낸다)")
    ap.add_argument("--keep", action="store_true", help="사본 · 환경을 지우지 않는다 (경로를 결과에 적는다)")
    a = ap.parse_args(argv)
    adr = read_adr(a.adr)
    root = Path(a.adr).resolve().parents[2]
    targets = load_targets(a.targets or root / "radar-targets.json")
    checks = json.loads(Path(a.checks or root / "radar-checks.json").read_text(encoding="utf-8"))
    package, version = a.package or adr["technology"], a.to or release_version(adr)
    if not version:
        raise SystemExit("올릴 버전을 결정 기록에서 못 읽었다 — --to로 넘긴다")
    try:
        repo, comp, check = choose_target(adr, targets, checks, a.repo, a.component)
    except ValueError as e:
        raise SystemExit(str(e))
    extra = []
    for spec in a.extra:
        name, sep, ver = spec.partition("==")
        if not sep or not name.strip() or not ver.strip():
            raise SystemExit(f"--with는 `패키지==버전` 꼴이다: {spec!r}")
        extra.append((name.strip(), ver.strip()))
    tag = "".join(f"+{norm(p)}-{v}" for p, v in extra)
    out = a.out or Path("pipeline/out/tryout") / f"{adr['id']}-{norm(package)}-{version}{tag}-{repo}-{comp}"
    res = tryout(adr, repo, targets[repo], comp, check, package, version, out, keep=a.keep, extra=extra, export=a.export)
    print(f"{res['package']} → {res['to']} ({repo} · {comp}): {res['verdict_text']}")
    for c in res.get("checks") or []:
        print(f"  {c['state']:6s} {c['script']}" + ("" if c["loaded"] else "  (이 패키지를 올리지 않았다)"))
    ex = res.get("export")
    if ex:
        print(f"내보내기: {ex['skipped_reason'] or '완료'}" + (f" · {ex['branch']}" if ex["branch"] else "") + (f" · {ex['pr_url']}" if ex["pr_url"] else ""))
    print(f"-> {out}")
    if ex and ex["branch"] and not ex["pr_url"] and (ex["skipped_reason"] or "").startswith(("내보내기 실패", "브랜치는 push")):
        return 2
    return 0 if res["verdict"] in ("pass", "already") else 1


if __name__ == "__main__":
    sys.exit(main())

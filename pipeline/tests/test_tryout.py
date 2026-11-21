"""올려 보기(pipeline/tryout.py, HYO-239) 오프라인 테스트 — 네트워크 · uv · 모델 없이.

  python pipeline/tests/test_tryout.py

가짜 대상 repo(임시 git)와 가짜 환경(설치 = 「설치된 버전」을 파일에 적는 것)으로 끝까지 돈다. 보는 것:
- 고정 줄만 고친다 (주석 · 다른 패키지 · 이름이 비슷한 패키지는 그대로) · 버전을 결정 기록에서 읽는다
- 대상 repo에 아무것도 쓰지 않는다 (작업 폴더 · 브랜치 · HEAD 그대로)
- 판정: 통과 / 깨짐 / 원래 실패는 올린 탓이 아니다 / 통과했지만 그 패키지를 안 올렸으면 「모름」 / 설치 실패 / 고정 줄 없음 / 이미 그 버전
뒤쪽 변이 검사(규율 3): 기준을 안 보거나 · 올렸는지를 안 보거나 · 고정 줄을 못 찾아도 넘어가면 위 테스트가 빨개져야 한다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import tryout  # noqa: E402

REQS = "# 앱\nzz-web==1.0.0  # 웹\nzz-web-extra==1.0.0\nzz_core[fast]==2.1 ; python_version >= '3.10'\nother>=3\n"
# 검사 스크립트 — 「설치된 버전」 파일을 읽는다. zz-web 2.x면 실패, zz_web을 올리는 것은 uses.py뿐
USES = "import json, sys\nsys.modules.setdefault('zz_web', sys)\nv = json.load(open('installed.json'))\nsys.exit(1 if v.get('zz-web', '').startswith('2.') else 0)\n"
PLAIN = "import sys\nsys.exit(0)\n"
BROKEN = "import sys\nsys.exit(3)\n"
# 부른 쪽 설정이 검사로 새면 실패한다 (HYO-116 — 실사용 앱의 RADAR_APP_MODE=real이 test_backend를 실사용 모드로 열었다). zz_web도 올린다
ENVCHECK = ("import os, sys\nsys.modules.setdefault('zz_web', sys)\n"
            "sys.exit(9 if os.environ.get('RADAR_APP_MODE') or os.environ.get('ANTHROPIC_API_KEY') else 0)\n")


class FakeEnv:
    """설치 = manifest의 고정 줄을 읽어 installed.json에 적는다. fail_on에 든 버전은 설치 실패."""

    def __init__(self, fail_on=()):
        self.python, self.fail_on, self.repo = Path(sys.executable), set(fail_on), None

    def create(self, log):
        return {"exit": 0, "cmd": ["fake"], "seconds": 0, "log": str(log)}

    def install(self, repo, files, log):
        self.repo, got = Path(repo), {}
        for f in files:
            for line in (Path(repo) / f).read_text(encoding="utf-8").splitlines():
                m = tryout.PIN.match(line)
                if m:
                    got[tryout.norm(m.group("name"))] = m.group("ver")
        bad = [v for v in got.values() if v in self.fail_on]
        if not bad:
            (Path(repo) / "installed.json").write_text(json.dumps(got), encoding="utf-8")
        Path(log).open("a", encoding="utf-8").write("No solution found\n" if bad else "ok\n")
        return {"exit": 1 if bad else 0, "cmd": ["fake"], "seconds": 0, "log": str(log)}

    def version_of(self, package):
        return json.loads((self.repo / "installed.json").read_text(encoding="utf-8")).get(tryout.norm(package))


# npm 픽스처 (HYO-248) — web/ 아래 package.json · lockfile. 검사는 node 대신 파이썬 스크립트가 모듈 훅 흉내(TRYOUT_PROBE_OUT에 이름 적기)를 낸다
PKG = {"name": "web", "private": True, "dependencies": {"zz-ui": "^1.0.0"}, "devDependencies": {"zz-ui-extra": "~1.0.0", "zz-odd": ">=1 <3"}}
LOCK = {"name": "web", "lockfileVersion": 3, "requires": True, "packages": {
    "": {"name": "web", "dependencies": {"zz-ui": "^1.0.0"}, "devDependencies": {"zz-ui-extra": "~1.0.0", "zz-odd": ">=1 <3"}},
    "node_modules/zz-odd": {"version": "1.0.0", "resolved": "https://r/zz-odd/-/zz-odd-1.0.0.tgz", "integrity": "sha512-odd", "dev": True},
    "node_modules/zz-ui": {"version": "1.0.0", "resolved": "https://r/zz-ui/-/zz-ui-1.0.0.tgz", "integrity": "sha512-1.0.0", "license": "MIT"},
    "node_modules/zz-ui-extra": {"version": "1.0.0", "resolved": "https://r/zz-ui-extra/-/zz-ui-extra-1.0.0.tgz", "integrity": "sha512-x", "dev": True}}}
NPM_USES = ("import json, os, sys\nout = os.environ.get('TRYOUT_PROBE_OUT')\nif out:\n    open(out, 'a').write('zz-ui\\tfile:///x/node_modules/zz-ui/index.js\\n')\n"
            "v = json.load(open('node_modules/zz-ui/package.json'))['version']\nsys.exit(1 if v.startswith('2.') else 0)\n")


def _npm_files(wc):
    (wc / "web").mkdir()
    for name, body in (("web/package.json", tryout._json_text(PKG)), ("web/package-lock.json", tryout._json_text(LOCK)),
                       ("web/uses.py", NPM_USES), ("web/plain.py", PLAIN)):
        (wc / name).write_text(body, encoding="utf-8")


class FakeNpmEnv:
    """npm ci = lockfile의 설치 버전을 node_modules/<이름>/package.json에 적는 것. fail_on 버전은 설치 실패 · missing 버전은 레지스트리에 없다."""

    def __init__(self, fail_on=(), missing=()):
        self.fail_on, self.missing = set(fail_on), set(missing)

    def view(self, name, version, log):
        return None if version in self.missing else {"tarball": f"https://r/{name}/-/{name}-{version}.tgz", "integrity": f"sha512-{version}"}

    def install(self, repo, rel, log):
        lock = json.loads((Path(repo) / rel / "package-lock.json").read_text(encoding="utf-8"))
        got = {k.split("node_modules/", 1)[1]: v["version"] for k, v in lock["packages"].items() if k.startswith("node_modules/")}
        bad = [v for v in got.values() if v in self.fail_on]
        for name, v in ({} if bad else got).items():
            (Path(repo) / rel / "node_modules" / name).mkdir(parents=True, exist_ok=True)
            (Path(repo) / rel / "node_modules" / name / "package.json").write_text(json.dumps({"name": name, "version": v}), encoding="utf-8")
        Path(log).open("a", encoding="utf-8").write("npm ERR! lock file out of sync\n" if bad else "ok\n")
        return {"exit": 1 if bad else 0, "cmd": ["fake npm ci"], "seconds": 0, "log": str(log)}

    def version_of(self, repo, rel, name):
        return tryout.NpmEnv().version_of(repo, rel, name)          # 설치 버전 읽기는 진짜와 같다


def _run_npm(d, runs, version="1.5.0", env=None, name="out", export=False, package="zz-ui", adr=None):
    wc = _target(d) if not (Path(d) / "target").exists() else Path(d) / "target"
    check = {"manifests": ["requirements.txt"], "install": ["requirements.txt"], "run": [{"script": "app/plain.py"}],
             "npm": {"dir": "web", "run": [{"cmd": [sys.executable, s]} for s in runs]}}
    return wc, tryout.tryout(adr or ADR, "t", str(wc), "web", check, package, version, Path(d) / name, env=FakeEnv(), npm_env=env or FakeNpmEnv(), export=export)


def _target(d):
    """임시 대상 repo — origin/main이 있는 작업 사본 (올려 보기는 origin/main에 맞춘다)."""
    up, wc = Path(d) / "up.git", Path(d) / "target"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(up)], check=True)
    subprocess.run(["git", "clone", "-q", str(up), str(wc)], check=True, capture_output=True)
    (wc / "app").mkdir()
    _npm_files(wc)
    for name, body in (("requirements.txt", REQS), ("app/uses.py", USES), ("app/plain.py", PLAIN), ("app/broken.py", BROKEN), ("app/envcheck.py", ENVCHECK)):
        (wc / name).write_text(body, encoding="utf-8")
    git = ["git", "-C", str(wc), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    subprocess.run(git + ["push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
    subprocess.run(git + ["remote", "set-head", "origin", "main"], check=True, capture_output=True)
    (wc / "local-only.txt").write_text("커밋 안 한 내 작업", encoding="utf-8")       # 사본에 따라오면 안 된다
    return wc


ADR = {"id": "0017", "technology": "zz-web", "use": "의존성 릴리스 2.0.0 대응 (breaking)", "title": "", "affected": [{"repo": "t", "component": "web"}]}


def _run(d, runs, package="zz-web", version="2.0.0", env=None, name="out", extra=(), out=None, export=False):
    wc = _target(d) if not (Path(d) / "target").exists() else Path(d) / "target"
    check = {"manifests": ["requirements.txt"], "install": ["requirements.txt"], "run": [{"script": s} for s in runs]}
    return wc, tryout.tryout(ADR, "t", str(wc), "web", check, package, version, out or Path(d) / name, env=env or FakeEnv(), extra=extra, export=export)


def test_bump_pin_touches_only_that_line():
    text, old = tryout.bump_pin(REQS, "zz-web", "2.0.0")
    assert old == "1.0.0" and text == REQS.replace("zz-web==1.0.0  # 웹", "zz-web==2.0.0  # 웹"), text      # 주석 그대로 · zz-web-extra는 다른 패키지
    text, old = tryout.bump_pin(REQS, "ZZ.Core", "3.0")                                                  # 이름 표기(_ · . · 대소문자)는 같은 패키지
    assert old == "2.1" and "zz_core[fast]==3.0 ; python_version >= '3.10'" in text, text
    assert tryout.bump_pin(REQS, "other", "4")[1] is None and tryout.bump_pin(REQS, "nope", "1")[1] is None   # 범위 고정(>=)은 고정 줄이 아니다
    crlf, old = tryout.bump_pin("a==1\r\nb==2\r\n", "b", "3")
    assert crlf == "a==1\r\nb==3\r\n" and old == "2"                                                     # 줄 끝은 그대로


def test_release_version_from_adr():
    rv = tryout.release_version
    assert rv({"use": "의존성 릴리스 0.162.6 대응 (breaking)"}) == "0.162.6"
    assert rv({"use": "의존성 릴리스 langchain-core==1.8.0 대응 (breaking)"}) == "1.8.0"
    assert rv({"use": "의존성 릴리스 v46.0.0-alpha.5 대응"}) == "46.0.0-alpha.5"
    assert rv({"use": "의존성 릴리스 2026-11-11 Node.js 보안 릴리스 대응 (security)"}) is None                    # 날짜는 버전이 아니다 — --to로 넘긴다
    assert rv({"use": "", "title": "FastAPI 0.162.6 대응"}) == "0.162.6"


def test_pass_needs_the_package_to_be_loaded():
    with tempfile.TemporaryDirectory() as d:
        _, r = _run(d, ["app/plain.py"])
        assert r["verdict"] == "pass_not_exercised" and r["checks"][0]["state"] == "통과" and not r["checks"][0]["loaded"], r
        _, r = _run(d, ["app/plain.py", "app/uses.py"], version="1.5.0", name="out2")
        assert r["verdict"] == "pass" and [c["loaded"] for c in r["checks"]] == [False, True], r
        assert r["pins"] == [{"package": "zz-web", "manifest": "requirements.txt", "from": "1.0.0", "to": "1.5.0"}] and (r["installed_before"], r["installed_after"]) == ("1.0.0", "1.5.0")
        assert "zz-web==1.5.0" in r["diff"] and "-zz-web==1.0.0" in r["diff"]
        text = (Path(d) / "out2" / "report.md").read_text(encoding="utf-8")
        assert "통과 — 올려도" in text and "| `app/uses.py` | 통과 | 통과 | 통과 | 예 |" in text, text


def test_fail_only_when_baseline_passed():
    with tempfile.TemporaryDirectory() as d:
        _, r = _run(d, ["app/uses.py", "app/broken.py"])
        assert r["verdict"] == "fail" and [c["state"] for c in r["checks"]] == ["깨짐", "원래 실패"], r
        _, r = _run(d, ["app/broken.py"], name="out2")
        assert r["verdict"] == "baseline_failed", r                                                    # 원래 실패뿐이면 기준이 없다 — 통과도 깨짐도 아니다
        _, r = _run(d, ["app/broken.py", "app/uses.py"], version="1.5.0", name="out3")
        # 원래 실패는 올린 탓이 아니다 — 그래도 남은 검사로 「통과」라 하지 않는다 (HYO-116: 일부만 · 내보내지 않는다)
        assert r["verdict"] == "pass_partial" and r["checks"][0]["state"] == "원래 실패" and not tryout.may_export(r["verdict"]), r
        _, r = _run(d, ["app/uses.py"], version="1.5.0", name="out4")
        assert r["verdict"] == "pass", r                                                               # 원래 실패가 없으면 통과


def test_check_env_drops_app_settings():
    base = {"PATH": "/bin", "HOME": "/h", "RADAR_APP_MODE": "real", "RADAR_REAL_ADR": "/adr", "ANTHROPIC_API_KEY": "k", "GEMINI_API_KEY": "g",
            "OPENAI_API_KEY": "o", "ELECTRON_RUN_AS_NODE": "1"}
    env = tryout.check_env(base)
    assert env == {"PATH": "/bin", "HOME": "/h", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}, env
    old = {k: os.environ.get(k) for k in ("RADAR_APP_MODE", "ANTHROPIC_API_KEY")}
    os.environ.update(RADAR_APP_MODE="real", ANTHROPIC_API_KEY="k")      # 부른 쪽(실사용 앱)에 있어도 검사에는 안 간다
    try:
        with tempfile.TemporaryDirectory() as d:
            _, r = _run(d, ["app/envcheck.py"], version="1.5.0")
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    assert r["verdict"] == "pass" and r["checks"][0]["base_exit"] == 0, r


def test_cannot_check_cases():
    with tempfile.TemporaryDirectory() as d:
        _, r = _run(d, ["app/uses.py"], env=FakeEnv(fail_on={"2.0.0"}))
        assert r["verdict"] == "install_failed" and "No solution found" in r["install_error"] and r["checks"][0]["state"] == "안 돌림", r
        _, r = _run(d, ["app/uses.py"], package="nope", name="o2")
        assert r["verdict"] == "no_pin" and not r["steps"], r                                          # 고정 줄이 없으면 설치도 하지 않는다
        _, r = _run(d, ["app/uses.py"], version="1.0.0", name="o3")
        assert r["verdict"] == "already", r
        _, r = _run(d, [], name="o4")
        assert r["verdict"] == "no_checks", r
        _, r = _run(d, ["app/uses.py"], env=FakeEnv(fail_on={"1.0.0"}), name="o5")
        assert r["verdict"] == "baseline_failed", r


def test_target_repo_is_not_touched():
    with tempfile.TemporaryDirectory() as d:
        wc = _target(d)
        git = lambda *a: subprocess.run(["git", "-C", str(wc), *a], capture_output=True, text=True).stdout   # noqa: E731
        before = (git("status", "--porcelain"), git("rev-parse", "HEAD"), git("branch", "-a"), git("worktree", "list"), (wc / "requirements.txt").read_text(encoding="utf-8"))
        _, r = _run(d, ["app/uses.py"], version="1.5.0")
        after = (git("status", "--porcelain"), git("rev-parse", "HEAD"), git("branch", "-a"), git("worktree", "list"), (wc / "requirements.txt").read_text(encoding="utf-8"))
        assert before == after, (before, after)
        assert r["verdict"] == "pass" and "origin/main" in r["based_on"] and "work" not in r, r        # 사본은 지운다
        assert "local-only" not in json.dumps(r, ensure_ascii=False)


def test_relative_out_and_bumping_together():
    """상대 경로 결과 폴더(기본값)로도 검사가 돈다 — 처음 실제 실행(langchain-core 1.8.0)에서 probe를 못 찾아 전부 「원래 실패」로 나왔다.
    혼자서는 설치가 안 되는 릴리스는 함께 올려 본다(--with) — 함께 올릴 패키지의 고정 줄이 없으면 일부만 올리지 않고 멈춘다."""
    import os
    with tempfile.TemporaryDirectory() as d:
        _target(d)
        cwd = os.getcwd()
        os.chdir(d)
        try:
            _, r = _run(d, ["app/uses.py"], version="1.5.0", out="rel-out")
        finally:
            os.chdir(cwd)
        assert r["verdict"] == "pass" and r["checks"][0]["loaded"], r
        alone = FakeEnv(fail_on={"1.5.0"})
        _, r = _run(d, ["app/uses.py"], version="1.5.0", env=alone, name="o2")
        assert r["verdict"] == "install_failed", r
        _, r = _run(d, ["app/uses.py"], version="1.6.0", extra=[("zz_core", "3.0")], name="o3")
        assert r["verdict"] == "pass" and [(p["package"], p["from"], p["to"]) for p in r["pins"]] == [("zz-web", "1.0.0", "1.6.0"), ("zz_core", "2.1", "3.0")], r
        assert "zz_core[fast]==3.0" in r["diff"] and "함께 올림: zz_core 3.0" in (Path(d) / "o3" / "report.md").read_text(encoding="utf-8")
        _, r = _run(d, ["app/uses.py"], version="1.6.0", extra=[("nope", "9")], name="o4")
        assert r["verdict"] == "no_pin" and r["no_pin_for"] == ["nope"] and not r["steps"], r


FAKE_GH = """#!/bin/sh
echo "$*" | tr '\\n' ' ' >> "$FAKE_GH_LOG"; echo >> "$FAKE_GH_LOG"
if [ "$1 $2" = "pr list" ]; then
  if [ -n "$FAKE_GH_LIST" ]; then echo "$FAKE_GH_LIST"; else echo "[]"; fi
elif [ "$1 $2" = "pr create" ]; then
  echo "https://github.com/o/t/pull/7"
fi
"""


def _with_fake_gh(d, fn, existing_pr=None):
    """가짜 gh를 PATH 맨 앞에 두고 fn을 돈다 → (fn 결과, gh 호출 줄들)."""
    import os
    b = Path(d) / "bin"
    b.mkdir(exist_ok=True)
    (b / "gh").write_text(FAKE_GH, encoding="utf-8")
    (b / "gh").chmod(0o755)
    log = Path(d) / "gh.log"
    log.write_text("", encoding="utf-8")
    old = {k: os.environ.get(k) for k in ("PATH", "FAKE_GH_LOG", "FAKE_GH_LIST")}
    os.environ.update(PATH=f"{b}{os.pathsep}{old['PATH']}", FAKE_GH_LOG=str(log), FAKE_GH_LIST=existing_pr or "")
    try:
        return fn(), log.read_text(encoding="utf-8").splitlines()
    finally:
        calls = log.read_text(encoding="utf-8").splitlines()
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _with_fake_gh.last = calls


def _heads(d):
    return subprocess.run(["git", "-C", str(Path(d) / "up.git"), "for-each-ref", "--format=%(refname:short)", "refs/heads"], capture_output=True, text=True).stdout.split()


def _up_git(d, *a):
    return subprocess.run(["git", "-C", str(Path(d) / "up.git"), *a], capture_output=True, text=True).stdout.strip()


def test_export_pushes_branch_and_opens_draft_pr():
    with tempfile.TemporaryDirectory() as d:
        wc = _target(d)
        (_, r), calls = _with_fake_gh(d, lambda: _run(d, ["app/uses.py"], version="1.5.0", export=True))
        br = "radar/tryout/zz-web-1.5.0"
        assert r["verdict"] == "pass" and r["export"] == {"branch": br, "pr_url": "https://github.com/o/t/pull/7", "skipped_reason": None}, r
        assert sorted(_heads(d)) == ["main", br], _heads(d)
        assert _up_git(d, "rev-list", "--count", f"main..{br}") == "1"                                  # 기본 브랜치 위에 커밋 하나
        assert _up_git(d, "diff", "--name-only", "main", br) == "requirements.txt"                      # 고정 줄 파일만 (installed.json 같은 검사 부산물은 안 넣는다)
        assert "zz-web==1.5.0" in _up_git(d, "show", f"{br}:requirements.txt")
        assert _up_git(d, "rev-parse", "main") == subprocess.run(["git", "-C", str(wc), "rev-parse", "origin/main"], capture_output=True, text=True).stdout.strip()
        create = [c for c in calls if c.startswith("pr create")]
        assert len(create) == 1 and "--draft" in create[0] and "--base main" in create[0], calls
        assert "zz-web 1.0.0 → 1.5.0 올려 보기 통과 (ADR 0017)" in create[0] and "레포 주인이 정합니다" in create[0], create
        assert not [c for c in calls if "merge" in c.split()[:2]]
        assert json.loads((Path(d) / "out" / "result.json").read_text(encoding="utf-8"))["export"]["pr_url"]      # result.json에도 적는다


def test_export_skips_non_pass_and_default_off():
    with tempfile.TemporaryDirectory() as d:
        _target(d)
        (_, r), c1 = _with_fake_gh(d, lambda: _run(d, ["app/plain.py"], version="1.5.0", export=True))        # 올린 검사가 없다 → pass_not_exercised
        assert r["verdict"] == "pass_not_exercised" and r["export"] == {"branch": None, "pr_url": None, "skipped_reason": "내보내지 않음: pass_not_exercised"}, r
        (_, r), c2 = _with_fake_gh(d, lambda: _run(d, ["app/uses.py", "app/broken.py"], name="o2", export=True))   # fail
        assert r["verdict"] == "fail" and r["export"]["skipped_reason"] == "내보내지 않음: fail", r
        (_, r), c3 = _with_fake_gh(d, lambda: _run(d, ["app/uses.py"], version="1.5.0", name="o3"))           # --export 없음
        assert r["verdict"] == "pass" and "export" not in r, r
        assert _heads(d) == ["main"] and c1 + c2 + c3 == [], (_heads(d), c1, c2, c3)


def test_export_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        _target(d)
        br = "radar/tryout/zz-web-1.5.0"
        _with_fake_gh(d, lambda: _run(d, ["app/uses.py"], version="1.5.0", export=True))
        before = _up_git(d, "rev-parse", br)
        (_, r), calls = _with_fake_gh(d, lambda: _run(d, ["app/uses.py"], version="1.5.0", name="o2", export=True))    # 브랜치만 있다
        assert r["export"]["pr_url"] is None and "이미 있다" in r["export"]["skipped_reason"], r
        assert not [c for c in calls if c.startswith("pr create")] and _up_git(d, "rev-parse", br) == before, calls
        (_, r), calls = _with_fake_gh(d, lambda: _run(d, ["app/uses.py"], version="1.5.0", name="o3", export=True), existing_pr='[{"url": "https://github.com/o/t/pull/3"}]')
        assert r["export"]["pr_url"] == "https://github.com/o/t/pull/3" and not [c for c in calls if c.startswith("pr create")], (r, calls)


def test_export_refuses_default_branch():
    with tempfile.TemporaryDirectory() as d:
        _target(d)
        orig, main_before = tryout.export_branch, None
        tryout.export_branch = lambda package, version: "main"
        try:
            main_before = _up_git(d, "rev-parse", "main")
            try:
                _with_fake_gh(d, lambda: _run(d, ["app/uses.py"], version="1.5.0", export=True))
                raise AssertionError("기본 브랜치로 내보내려는데 거부하지 않았다")
            except SystemExit as e:
                assert "기본 브랜치" in str(e), e
        finally:
            tryout.export_branch = orig
        assert _heads(d) == ["main"] and _up_git(d, "rev-parse", "main") == main_before


def test_npm_bump_touches_only_that_package():
    with tempfile.TemporaryDirectory() as d:
        wc = Path(d)
        _npm_files(wc)
        dep = tryout.npm_dep(wc, {"dir": "web"}, "ZZ-UI")                                             # 대소문자만 무시
        assert dep == {"name": "zz-ui", "section": "dependencies", "spec": "^1.0.0", "manifest": "web/package.json", "lock": "web/package-lock.json", "from": "1.0.0"}, dep
        assert tryout.npm_dep(wc, {"dir": "web"}, "zz_ui") is None and tryout.npm_dep(wc, {"dir": "web"}, "zz-web") is None   # npm은 _ · -를 다른 이름으로 본다
        assert tryout.npm_dep(wc, None, "zz-ui") is None                                              # npm 설정이 없으면 npm 경로가 아니다
        texts, pin = tryout.npm_bump(wc, dep, "1.5.0", {"tarball": "https://r/zz-ui/-/zz-ui-1.5.0.tgz", "integrity": "sha512-new"})
        assert pin == {"package": "zz-ui", "manifest": "web/package.json", "from": "1.0.0", "to": "1.5.0", "range": "^1.0.0 → ^1.5.0"}, pin
        pkg, lock = json.loads(texts["web/package.json"]), json.loads(texts["web/package-lock.json"])
        assert pkg == {**PKG, "dependencies": {"zz-ui": "^1.5.0"}}, pkg                                # 다른 의존성 · 앞머리(^)는 그대로
        assert lock["packages"]["node_modules/zz-ui"] == {"version": "1.5.0", "resolved": "https://r/zz-ui/-/zz-ui-1.5.0.tgz", "integrity": "sha512-new", "license": "MIT"}
        assert lock["packages"][""]["dependencies"] == {"zz-ui": "^1.5.0"} and lock["packages"]["node_modules/zz-ui-extra"] == LOCK["packages"]["node_modules/zz-ui-extra"]
        old = (wc / "web/package-lock.json").read_text(encoding="utf-8").splitlines()
        changed = [a for a, b in zip(old, texts["web/package-lock.json"].splitlines()) if a != b]
        assert len(changed) == 4 and len(old) == len(texts["web/package-lock.json"].splitlines()), changed   # 범위 · version · resolved · integrity 네 줄만
        odd = tryout.npm_dep(wc, {"dir": "web"}, "zz-odd")
        assert tryout.npm_bump(wc, odd, "2.0.0", {"tarball": "t", "integrity": "i"})[0] is None              # 범위(>=1 <3)는 고칠 꼴이 아니다 — 지어내지 않는다
        (wc / "web/package.json").write_text(json.dumps(PKG, indent=4) + "\n", encoding="utf-8")
        texts, why = tryout.npm_bump(wc, dep, "1.5.0", {"tarball": "t", "integrity": "i"})
        assert texts is None and "같은 모양" in why, why                                              # 들여쓰기가 다르면 통째로 다시 쓰지 않는다


def test_npm_tryout_judges_like_python():
    with tempfile.TemporaryDirectory() as d:
        _, r = _run_npm(d, ["uses.py"])
        assert r["ecosystem"] == "npm" and r["verdict"] == "pass" and r["checks"][0]["loaded"], r
        assert (r["installed_before"], r["installed_after"]) == ("1.0.0", "1.5.0") and r["pins"][0]["range"] == "^1.0.0 → ^1.5.0", r
        assert '-    "zz-ui": "^1.0.0"' in r["diff"] and '+      "version": "1.5.0",' in r["diff"], r["diff"]
        assert "범위 ^1.0.0 → ^1.5.0" in (Path(d) / "out" / "report.md").read_text(encoding="utf-8")
        _, r = _run_npm(d, ["plain.py"], name="o2")
        assert r["verdict"] == "pass_not_exercised" and not r["checks"][0]["loaded"], r              # 그 패키지를 안 부른 검사로는 모른다
        _, r = _run_npm(d, ["uses.py"], version="2.0.0", name="o3")
        assert r["verdict"] == "fail" and r["checks"][0]["state"] == "깨짐", r


def test_npm_cannot_check_cases():
    with tempfile.TemporaryDirectory() as d:
        _, r = _run_npm(d, ["uses.py"], env=FakeNpmEnv(missing={"1.5.0"}))
        assert r["verdict"] == "install_failed" and "npm view" in r["install_error"] and not r.get("pins"), r
        _, r = _run_npm(d, ["uses.py"], env=FakeNpmEnv(fail_on={"1.5.0"}), name="o2")
        assert r["verdict"] == "install_failed" and "lockfile" in r["note"] and r["checks"][0]["state"] == "안 돌림", r
        _, r = _run_npm(d, ["uses.py"], version="1.0.0", name="o3")
        assert r["verdict"] == "already", r
        _, r = _run_npm(d, ["uses.py"], package="zz-odd", version="2.0.0", name="o4")
        assert r["verdict"] == "no_pin" and "고칠 수 있는 꼴" in r["note"], r
        _, r = _run_npm(d, ["uses.py"], package="zz-web", name="o5")
        assert r.get("ecosystem") is None and r["verdict"] == "pass_not_exercised", r                 # package.json에 없으면 Python 경로 그대로


def test_npm_export_commits_manifest_and_lock_only():
    with tempfile.TemporaryDirectory() as d:
        _target(d)
        adr = {**ADR, "path": str(Path(d) / "adr" / "docs" / "adr" / "0017-zz-ui.md")}
        (_, r), calls = _with_fake_gh(d, lambda: _run_npm(d, ["uses.py"], export=True, adr=adr))
        br = "radar/tryout/zz-ui-1.5.0"
        assert r["verdict"] == "pass" and r["export"]["branch"] == br and r["export"]["pr_url"], r
        assert _up_git(d, "diff", "--name-only", "main", br).split() == ["web/package-lock.json", "web/package.json"]   # node_modules · 프로브 부산물은 안 넣는다
        create = [c for c in calls if c.startswith("pr create")]
        assert len(create) == 1 and "`docs/adr/0017-zz-ui.md`" in create[0] and str(d) not in create[0], create      # 이 기기의 절대 경로를 PR에 적지 않는다
        assert "범위 ^1.0.0 → ^1.5.0" in create[0], create


def _node_hooks():
    node = shutil.which("node")
    if not node:
        return None
    r = subprocess.run([node, "-e", "process.exit(typeof require('node:module').registerHooks === 'function' ? 0 : 1)"], capture_output=True)
    return node if r.returncode == 0 else None


def test_npm_probe_counts_loads_not_resolves():
    """진짜 node로 모듈 훅을 본다 — 불러온(require) 검사만 「올렸다」, 경로만 찾은(import.meta.resolve) 검사는 아니다. node 22.15 미만이면 건너뛴다.
    require.resolve는 훅을 아예 안 지나서 둘을 못 가른다 — 경로 찾기는 import.meta.resolve로 본다."""
    node = _node_hooks()
    if not node:
        print("    (건너뜀: registerHooks가 있는 node가 없다)")
        return
    with tempfile.TemporaryDirectory() as d:
        mod = Path(d) / "node_modules" / "zz-ui"
        mod.mkdir(parents=True)
        (mod / "package.json").write_text('{"name": "zz-ui", "version": "1.0.0", "main": "index.js"}', encoding="utf-8")
        (mod / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
        out = Path(d) / "out"
        out.mkdir()
        rows = tryout.run_npm_checks(d, ".", [{"cmd": [node, "-e", "require('zz-ui')"]}, {"cmd": [node, "--input-type=module", "-e", "import.meta.resolve('zz-ui')"]},
                                              {"cmd": [node, "-e", "require('child_process').execFileSync(process.execPath, ['-e', \"require('zz-ui')\"])"]}],
                                     "zz-ui", out, "t")
    assert [r["exit"] for r in rows] == [0, 0, 0] and [r["loaded"]["zz-ui"] for r in rows] == [True, False, True], rows   # 자식 node 프로세스도 본다


def test_judge_table():
    row = lambda e, loaded=False: {"script": "s", "exit": e, "loaded": {"zz_web": loaded}}   # noqa: E731
    j = lambda b, a: tryout.judge(b, a, "zz-web")[0]   # noqa: E731
    assert j([row(0)], [row(0, True)]) == "pass" and j([row(0)], [row(0)]) == "pass_not_exercised"
    assert j([row(0)], [row(1, True)]) == "fail" and j([row(1)], [row(1)]) == "baseline_failed"
    assert j([row(1), row(0)], [row(0, True), row(0)]) == "pass_not_exercised"                           # 원래 실패하던 검사가 올린 것은 근거로 안 쓴다
    assert j([row(1), row(0)], [row(1), row(0, True)]) == "pass_partial"                                 # 올린 검사는 통과 · 원래 실패도 있다 → 일부만 (HYO-116)
    assert set(tryout.VERDICTS) >= {"pass", "pass_not_exercised", "pass_partial", "fail", "install_failed", "not_applied", "baseline_failed",
                                    "no_pin", "no_checks", "already"}


MUTATIONS = [
    ("기준을 안 본다 — 원래 실패도 깨짐으로", "judge", lambda base, after, package: tryout._judge0([{**b, "exit": 0} for b in base], after, package), test_fail_only_when_baseline_passed),
    ("그 패키지를 올렸는지 안 본다", "import_name", lambda package: "sys", test_pass_needs_the_package_to_be_loaded),
    ("고정 줄을 못 찾아도 넘어간다", "bump_pin", lambda text, package, version: (text, "0"), test_cannot_check_cases),
    ("함께 올릴 패키지의 고정 줄이 없어도 일부만 올린다", "norm", lambda name: "x", test_relative_out_and_bumping_together),
    ("이름이 비슷한 패키지도 고친다", "norm", lambda name: (name or "").strip().lower()[:6], test_bump_pin_touches_only_that_line),
    ("통과가 아니어도 내보낸다", "may_export", lambda verdict: True, test_export_skips_non_pass_and_default_off),
    ("PR을 draft로 안 연다", "GH_CREATE_FLAGS", [], test_export_pushes_branch_and_opens_draft_pr),
    ("manifest 밖 파일도 커밋한다", "files_to_add", lambda texts: ["."], test_export_pushes_branch_and_opens_draft_pr),
    ("열린 PR이 있어도 다시 낸다", "open_prs", lambda repo, branch: [], test_export_is_idempotent),
    ("같은 브랜치가 있어도 다시 push한다", "remote_has_branch", lambda repo, branch: False, test_export_is_idempotent),
    ("기본 브랜치에도 내보낸다", "is_default_branch", lambda branch, base: False, test_export_refuses_default_branch),
    ("날짜도 버전으로 읽는다", "_VER", __import__("re").compile(r"(\d[\d.\-]+\d)"), test_release_version_from_adr),
    ("원래 실패가 있어도 통과", "judge", lambda base, after, package: (lambda v: ("pass" if v[0] == "pass_partial" else v[0], v[1]))(
        tryout._judge0(base, after, package)), test_fail_only_when_baseline_passed),
    ("부른 쪽 설정이 검사로 샌다", "check_env", lambda base=None: {**(os.environ if base is None else base)}, test_check_env_drops_app_settings),
    ("npm: 그 패키지를 불렀는지 안 본다", "run_npm_checks", lambda repo, rel, runs, name, out, tag, timeout=1800: [
        {**r, "loaded": {name: True}} for r in tryout._run_npm_checks0(repo, rel, runs, name, out, tag, timeout)], test_npm_tryout_judges_like_python),
    ("npm: 범위 꼴을 안 보고 고친다", "NPM_SPEC", __import__("re").compile(r"^(?P<pre>)"), test_npm_bump_touches_only_that_package),
    ("npm: PR 본문에 이 기기의 절대 경로", "adr_ref", lambda adr: adr.get("path") or "", test_npm_export_commits_manifest_and_lock_only),
]
if _node_hooks():   # 진짜 node가 있을 때만 — 훅이 경로 찾기(resolve)까지 세면 잡혀야 한다
    MUTATIONS.append(("npm: 경로만 찾아도 불렀다고 센다", "NPM_PROBE",
                      tryout.NPM_PROBE.replace("load(url, context, nextLoad) {", "resolve(url, context, nextLoad) {").replace(
                          "return nextLoad(url, context)", "const r = nextLoad(url, context)\n      for (const n of names) if (r.url.includes(`/node_modules/${n}/`)) appendFileSync(out, `${n}\\t${r.url}\\n`)\n      return r"),
                      test_npm_probe_counts_loads_not_resolves))


def run_mutations():
    caught = 0
    tryout._judge0, tryout._run_npm_checks0 = tryout.judge, tryout.run_npm_checks
    for name, attr, repl, test in MUTATIONS:
        orig = getattr(tryout, attr)
        setattr(tryout, attr, repl)
        try:
            test()
            print(f"  안 잡힘  변이: {name}")
        except Exception:  # noqa: BLE001
            caught += 1
            print(f"  잡힘  변이: {name}")
        finally:
            setattr(tryout, attr, orig)
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

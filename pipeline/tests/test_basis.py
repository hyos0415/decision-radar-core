"""판정 기준(pipeline/basis.py, HYO-162) 오프라인 테스트 — 임시 git repo + 로컬 bare 원격만.

  python pipeline/tests/test_basis.py

11/2 사고를 그대로 재현한다: 관문은 Electron 39에서 판정 → 그 뒤 원격에 45로 올림 → detect가 묵은 목록으로 「v45 검토」 초안.
- 원격 HEAD를 읽는다: 작업 사본이 묵어도(39) 지금 기준은 45 (나)
- 관문 기준 39 → 지금 45, 소식 v45.0.0 → 「이미 반영됨」으로 빠진다 (가). 기준이 같으면 아무것도 안 빠진다
- 원격을 못 읽으면 작업 사본으로 읽되 그렇다고 남긴다
뒤쪽 변이 검사(규율 3): 원격 대신 작업 사본을 읽거나, 버전 비교·바뀐 레포 판정을 망가뜨리면 위 테스트가 빨개져야 한다.
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

from pipeline import basis  # noqa: E402

os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


def git(cwd, *args):
    p = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, f"git {args}: {p.stderr}"
    return p.stdout.strip()


def _cfg(d):
    for k, v in (("user.name", "radar-test"), ("user.email", "radar@example.invalid"), ("commit.gpgsign", "false")):
        git(d, "config", k, v)


def _pkg(d, electron):
    (Path(d) / "desktop").mkdir(exist_ok=True)
    (Path(d) / "desktop" / "package.json").write_text(json.dumps({"dependencies": {"electron": electron}}), encoding="utf-8")


class World:
    """부모 폴더: app.git(원격) · app(작업 사본, 묵는다) · dev(원격에 올리는 다른 기기) · adr/radar-targets.json."""

    def __init__(self, origin=True):
        self.tmp = Path(tempfile.mkdtemp(prefix="basis-"))
        self.remote = self.tmp / "app.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.dev = self.tmp / "dev"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.dev)], check=True, capture_output=True)
        _cfg(self.dev)
        _pkg(self.dev, "^39.2.6")
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-q", "-m", "electron 39")
        git(self.dev, "push", "-q", "origin", "HEAD:main")
        self.work = self.tmp / "app"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.work)], check=True, capture_output=True)
        if not origin:
            git(self.work, "remote", "remove", "origin")
        (self.tmp / "adr").mkdir()
        self.targets = self.tmp / "adr" / "radar-targets.json"
        self.targets.write_text(json.dumps([{"repo": "app", "path": "../app", "exclude": ["fixtures"]},
                                            {"repo": "gone", "path": "../gone"}]), encoding="utf-8")
        self.cache = self.tmp / "cache"

    def upgrade(self, electron="^45.0.0"):
        """다른 기기가 원격에 올린다 — 작업 사본(app)은 당기지 않는다 (맥 clone이 묵은 것과 같다)."""
        _pkg(self.dev, electron)
        git(self.dev, "commit", "-qam", f"electron {electron}")
        git(self.dev, "push", "-q", "origin", "HEAD:main")

    def basis(self):
        return basis.materialize(self.targets, self.cache)


FEED = [
    {"id": "github-release:electron/electron@v45.0.0", "kind": "dependency_release", "package": "electron", "version": "v45.0.0",
     "url": "https://github.com/electron/electron/releases/tag/v45.0.0"},
    {"id": "github-release:electron/electron@v46.0.0", "kind": "dependency_release", "package": "electron", "version": "v46.0.0",
     "url": "https://github.com/electron/electron/releases/tag/v46.0.0"},
    {"id": "vendor-diff:acme", "kind": "page_change", "url": "https://acme.example/pricing"},
]


def _repo(b, name):
    return next(r for r in b["repos"] if r["repo"] == name)


def test_reads_remote_head_not_stale_clone():
    w = World()
    _, old = w.basis()
    w.upgrade()
    derived, new = w.basis()
    app = _repo(new, "app")
    assert app["source"] == "remote" and app["commit"] == git(w.remote, "rev-parse", "main"), app
    assert app["versions"]["electron"] == ["45.0.0"], app["versions"]           # 작업 사본은 아직 39
    assert json.loads((w.work / "desktop" / "package.json").read_text(encoding="utf-8"))["dependencies"]["electron"] == "^39.2.6"
    assert _repo(old, "app")["commit"] != app["commit"]
    t = {x["repo"]: x for x in json.loads(Path(derived).read_text(encoding="utf-8"))}
    assert Path(t["app"]["path"]).resolve() != w.work.resolve() and t["app"]["exclude"] == ["fixtures"], t
    assert _repo(new, "gone")["source"] == "missing" and "gone" in t


def test_already_applied_dropped():
    """회귀 케이스: 관문 기준 39 → 지금 45, 소식 v45.0.0 → 초안 대상 0 · excluded 1. v46과 페이지 변경은 그대로."""
    w = World()
    _, old = w.basis()
    w.upgrade()
    _, new = w.basis()
    keep, out = basis.applied(FEED, old, new)
    assert [e["id"] for e in keep] == [FEED[1]["id"], FEED[2]["id"]], keep
    assert len(out) == 1 and out[0]["url"] == FEED[0]["url"] and out[0]["reason"].startswith("이미 반영됨") and "45.0.0" in out[0]["reason"], out


def test_same_basis_drops_nothing():
    w = World()
    _, old = w.basis()
    _, new = w.basis()
    assert basis.changed(old, new) == {}
    assert basis.applied(FEED, old, new) == (FEED, [])


def test_already_at_gate_time_not_ours():
    """관문 때 이미 ≥였던 릴리스는 이 검사의 몫이 아니다 — 판정 시점이 묵은 것만 바로잡는다."""
    w = World()
    w.upgrade("^45.0.0")
    _, old = w.basis()
    w.upgrade("^46.0.0")
    _, new = w.basis()
    keep, out = basis.applied(FEED, old, new)
    assert [e["url"] for e in out] == [FEED[1]["url"]], out                     # v46만 — v45는 관문 때도 이미 45였다
    assert FEED[0] in keep


def test_versions_compare_as_numbers():
    """10.0.0 ≥ 9.1.0 — 문자열로 견주면 거꾸로 나온다."""
    old = {"repos": [{"repo": "app", "commit": "a" * 40, "versions": {"vllm": ["0.8.0"]}}]}
    new = {"repos": [{"repo": "app", "commit": "b" * 40, "versions": {"vllm": ["0.10.0"]}}]}
    feed = [{"package": "vLLM", "version": "v0.9.1", "url": "u1"}, {"package": "vLLM", "version": "v0.11.0", "url": "u2"}]
    keep, out = basis.applied(feed, old, new)
    assert [e["url"] for e in out] == ["u1"] and [e["url"] for e in keep] == ["u2"], (keep, out)


def test_missing_basis_drops_nothing():
    w = World()
    _, new = w.basis()
    assert basis.applied(FEED, None, new) == (FEED, [])
    assert basis.applied(FEED, {"repos": []}, new) == (FEED, [])


def test_no_origin_falls_back_loudly():
    w = World(origin=False)
    _, b = w.basis()
    app = _repo(b, "app")
    assert app["source"] == "local" and "origin" in app["error"] and app["commit"] == git(w.work, "rev-parse", "HEAD"), app
    assert app["versions"]["electron"] == ["39.2.6"]
    line = basis.summary_line(b)
    assert "작업 사본 1개" in line and "app" in line and "폴더 없음 1개" in line, line


def test_unreadable_targets():
    d = Path(tempfile.mkdtemp(prefix="basis-"))
    derived, b = basis.materialize(d / "nope.json", d / "cache")
    assert derived is None and "대상 목록을 못 읽었다" in b["error"] and basis.changed(b, b) == {}
    assert "작업 사본으로 돈다" in basis.summary_line(b)


def test_roundtrip_file():
    w = World()
    _, b = w.basis()
    p = w.tmp / "x" / basis.BASIS
    basis.write(b, p)
    assert basis.load(p) == b and basis.load(w.tmp / "none.json") is None


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_tree, orig_changed, orig_vtuple = basis.remote_tree, basis.changed, basis.vtuple

    def local_tree(name, root, cache_dir):  # 원격 대신 작업 사본 — 11/2와 같은 묵은 읽기
        return {"repo": name, "source": "remote", "commit": git(root, "rev-parse", "HEAD"), "root": str(root)}

    return [
        ("원격 대신 작업 사본을 읽음", lambda: setattr(basis, "remote_tree", local_tree), lambda: setattr(basis, "remote_tree", orig_tree)),
        ("바뀐 레포를 못 찾음", lambda: setattr(basis, "changed", lambda o, n: {}), lambda: setattr(basis, "changed", orig_changed)),
        ("모든 레포를 바뀐 것으로 봄", lambda: setattr(basis, "changed", lambda o, n: {r["repo"]: ("0" * 7, "1" * 7) for r in (n or {}).get("repos") or []}),
         lambda: setattr(basis, "changed", orig_changed)),
        ("버전 비교를 문자열로", lambda: setattr(basis, "vtuple", lambda s: str(s or "").lstrip("v^") or None), lambda: setattr(basis, "vtuple", orig_vtuple)),
    ]


def run_mutations():
    missed = 0
    for name, apply, undo in mutations():
        apply()
        try:
            caught = False
            for _, f in TESTS:
                try:
                    f()
                except Exception:  # noqa: BLE001
                    caught = True
                    break
        finally:
            undo()
        print(f"  {'잡힘' if caught else '놓침'} {name}")
        missed += not caught
    return missed


def main():
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
    if bad:
        return 1
    print("변이 검사 — 망가뜨린 코드를 테스트가 잡아야 한다")
    missed = run_mutations()
    print(f"변이 {len(mutations())}개 중 {len(mutations()) - missed}개 잡힘")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())

"""수집기 오프라인 테스트 — 네트워크 없이 파서·합치기·간격·규칙을 확인한다.

  python collector/tests/test_collector.py
"""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from collector import fetch  # noqa: E402
from collector.item import Item, extract_links, html_to_text, merge, text_hash  # noqa: E402
from collector.sources import github, hn, huggingface, reddit, vendor  # noqa: E402

REDDIT_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <author><name>/u/someone</name></author>
  <category term="LocalLLaMA" label="r/LocalLLaMA"/>
  <content type="html">&lt;div&gt;New 4B model &lt;a href="https://huggingface.co/acme/Swift-4B"&gt;HF&lt;/a&gt;&lt;/div&gt;
   submitted by &lt;a href="https://www.reddit.com/user/someone"&gt;/u/someone&lt;/a&gt;
   &lt;a href="https://github.com/acme/swift"&gt;[link]&lt;/a&gt;
   &lt;a href="https://www.reddit.com/r/LocalLLaMA/comments/abc123/swift/"&gt;[comments]&lt;/a&gt;</content>
  <id>t3_abc123</id>
  <link href="https://www.reddit.com/r/LocalLLaMA/comments/abc123/swift/"/>
  <published>2026-09-30T10:00:00+00:00</published>
  <title>Swift 4B released</title>
 </entry>
</feed>"""

FINEPRINT_STACK = """## 스택
### 의존성 → component

| 의존성 (`requirements.txt`) | component |
|---|---|
| fastapi · pydantic · jinja2 · uvicorn | web |
| pdfplumber | ingest |
| vLLM (docker `vllm/vllm-openai:latest`, `scripts/run_vllm.sh`) | pilot |
| llama.cpp `llama-server` | analysis |
| k6 (외부 도구) | tools |

### 외부 모델·서비스

| 모델·서비스 | component |
|---|---|
| Anthropic `claude-haiku-4-5-20251001` (조건 추출) | analysis |
| NVIDIA API `nvidia/nemotron-3-ultra-550b-a55b` · `google/gemma-4-31b-it` (비교) | eval |
| 금융감독원 Finlife API (예금·적금·회사) | ingest |
| Langfuse Cloud (US) — 프롬프트 | verifier |

### 평가셋
"""

VENDORS = {"vendors": {"anthropic": {"match": ["anthropic", "claude"], "feeds": []},
                       "langfuse": {"match": ["langfuse"], "feeds": []},
                       "aws-bedrock": {"match": ["bedrock"], "feeds": []}},
           "excluded": [{"match": ["nvidia"], "why": "js"}, {"match": ["금융감독원", "finlife"], "why": "data"}]}


def test_reddit_parse_links_and_id():
    items = reddit.parse(REDDIT_ATOM, "r/LocalLLaMA:top-week", "LocalLLaMA", None)
    assert len(items) == 1
    it = items[0]
    assert it.id == "reddit:t3_abc123"
    assert it.links == ["https://huggingface.co/acme/Swift-4B", "https://github.com/acme/swift"], it.links
    assert it.signals == {"rank:r/LocalLLaMA:top-week": 1}
    assert it.meta["subreddit"] == "LocalLLaMA"


def test_reddit_urls_cover_flairs():
    cfg = {"limit": 100, "subreddits": [{"name": "LocalLLaMA", "flairs": ["New Model"]}, {"name": "Rag"}]}
    got = list(reddit.urls(cfg))
    assert [g[0] for g in got] == ["r/LocalLLaMA:top-week", "r/LocalLLaMA:flair:New Model", "r/Rag:top-week"]
    assert "flair%3A%22New%20Model%22" in got[1][1] and "t=week" in got[1][1]


def test_merge_combines_channels():
    a = reddit.parse(REDDIT_ATOM, "r/LocalLLaMA:top-week", "LocalLLaMA", None)[0]
    b = reddit.parse(REDDIT_ATOM, "r/LocalLLaMA:flair:New Model", "LocalLLaMA", "New Model")[0]
    m = merge([a, b])
    assert len(m) == 1
    assert m[0].channels == ["r/LocalLLaMA:top-week", "r/LocalLLaMA:flair:New Model"]
    assert m[0].meta["flair_query"] == "New Model"
    assert len(m[0].signals) == 2      # 통로마다 순위가 따로 남는다


def test_hn_windows_lag_and_span():
    now = 1_800_000_000
    ws = list(hn.windows({"lag_days": 2, "window_days": 7}, now))
    assert len(ws) == 7
    assert ws[0][0] == now - 9 * 86400 and ws[-1][1] == now - 2 * 86400
    assert all(b - a == 86400 for a, b in ws)


def test_hn_parse_self_post_url():
    items = hn.parse([{"objectID": "42", "title": "Ask HN: x", "points": 30, "num_comments": 5, "url": None,
                       "story_text": 'see <a href="https://example.com/a">a</a>', "created_at": "2026-09-28T00:00:00Z"}])
    it = items[0]
    assert it.url == "https://news.ycombinator.com/item?id=42"
    assert it.links == ["https://example.com/a"]
    assert it.signals == {"points": 30, "comments": 5}


def test_github_dependencies_from_claude_md():
    names = github.dependencies_from_claude_md(FINEPRINT_STACK)
    assert names == ["fastapi", "pydantic", "jinja2", "uvicorn", "pdfplumber", "vLLM", "llama.cpp", "k6"], names


def test_github_queries():
    now = time.mktime((2026, 10, 2, 12, 0, 0, 0, 0, 0)) - time.timezone
    cfg = {"new_repos": {"created_within_days": 7, "min_stars": 100},
           "slow_growers": {"created_from_days": 30, "created_to_days": 7, "min_stars": 500}}
    q = dict(github.queries(cfg, now))
    assert q["new-repos"] == "created:>2026-09-25 stars:>=100", q
    assert q["slow-growers"] == "created:2026-09-02..2026-09-25 stars:>=500", q


def test_github_resolve_override_before_pypi():
    class NoNet:
        def get(self, url):
            raise AssertionError("override가 있으면 PyPI를 부르지 않는다")
    assert github.resolve_repo("vLLM", {"vllm": "vllm-project/vllm"}, NoNet(), {}) == "vllm-project/vllm"


def test_github_repo_from_pypi_skips_sponsors():
    # 실측 (2026-10-02): pydantic의 project_urls 첫 GitHub 링크가 후원 페이지였다
    info = {"project_urls": {"Funding": "https://github.com/sponsors/samuelcolvin",
                             "Source": "https://github.com/pydantic/pydantic",
                             "Documentation": "https://docs.pydantic.dev"}}
    assert github.repo_from_pypi_info(info) == "pydantic/pydantic"
    only_sponsor = {"project_urls": {"Funding": "https://github.com/sponsors/Kludex"}, "home_page": ""}
    assert github.repo_from_pypi_info(only_sponsor) is None


def _rel(tag, iso):
    return {"tag_name": tag, "name": tag, "html_url": f"https://github.com/o/r/releases/tag/{tag}",
            "published_at": iso, "body": "fix <https://example.com/x>", "prerelease": False, "draft": False}


def test_github_releases_paginate_until_window():
    # releases.atom은 10건뿐 — llama.cpp는 하루치도 안 됐다 (2026-10-02). API는 창을 벗어날 때까지 넘긴다
    since = time.mktime((2026, 9, 25, 0, 0, 0, 0, 0, 0)) - time.timezone
    page1 = [_rel(f"b{i}", "2026-10-01T00:00:00Z") for i in range(100)]          # 전부 창 안
    page2 = [_rel("b100", "2026-09-30T00:00:00Z"), _rel("old", "2026-09-01T00:00:00Z")]

    class F:
        calls = []
        def get_json(self, url, headers=None):
            self.calls.append(url)
            return page1 if url.endswith("&page=1") else page2   # "page=1" in url은 per_page=100에도 걸린다
    f = F()
    rep = {"requests": 0, "failed": []}
    items = github.fetch_releases("o/r", f, {}, since, rep, "dependency-releases")
    assert len(f.calls) == 2, f.calls                       # 100건이 다 창 안이라 2쪽까지
    assert len(items) == 101 and all(i.id.startswith("github-release:o/r@") for i in items)
    assert "old" not in {i.meta["tag"] for i in items}       # 창 밖은 버린다
    assert items[0].links == ["https://example.com/x"]


DESKTOP_STACK = """### 의존성 → component

| 의존성 | component |
|---|---|
| PyYAML | skill |
| Electron 39 · electron-vite 5 · React 19 · Geist 글꼴 (npm, `desktop/package.json`) | desktop |
| litellm (별도 venv) | product |
"""


def _write_pkg(d, obj):
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps(obj), encoding="utf-8")


def test_collect_only_targets_are_separate_from_my_stack():
    import os
    import tempfile
    from collector import run
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        app = t / "teammate-app"
        _write_pkg(app, {"dependencies": {"next": "15"}})
        (app / "CLAUDE.md").write_text("### 의존성 → component\n", encoding="utf-8")
        f = t / "collect-only.json"
        f.write_text(json.dumps({"targets": [{"repo": "teammate-app", "path": str(app)},          # 절대 경로도 받는다
                                             {"repo": "not-cloned", "path": str(t / "nope")}]}), encoding="utf-8")
        mine = t / "radar-targets.json"
        mine.write_text(json.dumps([]), encoding="utf-8")
        old = {k: os.environ.get(k) for k in ("RADAR_COLLECT_ONLY", "RADAR_TARGETS")}
        os.environ["RADAR_COLLECT_ONLY"], os.environ["RADAR_TARGETS"] = str(f), str(mine)
        try:
            texts, roots, info = run.collect_only_targets()
            assert set(roots) == {"teammate-app"} and set(texts) == {"teammate-app"}
            assert info["repos"] == ["teammate-app"] and [m["repo"] for m in info["missing"]] == ["not-cloned"]   # 조용히 빼지 않는다
            assert run.targets()[0] == {} and run.target_roots() == {}           # 내 스택(pipeline·스킬이 읽는 쪽)에는 섞이지 않는다
            assert github.dependencies_from_package_json(roots["teammate-app"]) == ["next"]
            try:
                run.merge_targets({"teammate-app": "x"}, roots, "repo 루트")
                raise AssertionError("이름이 겹치는데 합쳤다")
            except SystemExit:
                pass
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    shipped = json.loads(run.COLLECT_ONLY.read_text(encoding="utf-8"))["targets"]           # 저장소의 목록: 형식만 본다 (항목은 사람이 늘린다)
    assert all(t.get("repo") and t.get("path") for t in shipped)
    assert len({t["repo"] for t in shipped}) == len(shipped)


def test_collect_only_refresh_pulls_ff_and_reports_failures():
    import os
    import subprocess
    import tempfile
    from collector import run

    def git(cwd, *a):
        subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        up = t / "upstream"
        up.mkdir()
        git(up, "init", "-q", "-b", "main")
        git(up, "config", "user.email", "t@example.invalid")
        git(up, "config", "user.name", "t")
        _write_pkg(up, {"dependencies": {"react": "19"}})
        git(up, "add", ".")
        git(up, "commit", "-q", "-m", "1")
        subprocess.run(["git", "clone", "-q", str(up), str(t / "mirror")], check=True, capture_output=True)
        _write_pkg(up, {"dependencies": {"react": "19", "vite": "7"}})                           # 상류가 바뀐다
        git(up, "commit", "-q", "-am", "2")
        (t / "plain").mkdir()
        rep = run.refresh_collect_only({"mirror": t / "mirror", "plain": t / "plain"})
        assert rep["pulled"] == ["mirror"] and [f["repo"] for f in rep["failed"]] == ["plain"]
        assert github.dependencies_from_package_json(t / "mirror") == ["react", "vite"]        # 당긴 뒤의 스택을 읽는다
        mine = t / "radar-targets.json"
        mine.write_text(json.dumps([{"repo": "gone", "path": "gone", "mirror": True}, {"repo": "here", "path": "mirror", "mirror": True},
                                    {"repo": "working", "path": "plain"}]), encoding="utf-8")
        old = os.environ.get("RADAR_TARGETS")
        os.environ["RADAR_TARGETS"] = str(mine)
        try:
            assert [m["repo"] for m in run.my_targets_missing()] == ["gone"]                   # 내 스택도 조용히 빠지지 않는다
            assert list(run.my_mirror_roots()) == ["here"]                                    # mirror 표시가 있고 폴더가 있는 것만 당긴다 (작업 사본 제외)
        finally:
            if old is None:
                os.environ.pop("RADAR_TARGETS", None)
            else:
                os.environ["RADAR_TARGETS"] = old


def test_github_dependencies_from_package_json():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _write_pkg(root / "desktop", {"dependencies": {"react": "^19", "@radix-ui/react-dialog": "^1"},
                                      "devDependencies": {"electron": "^39", "react": "^19"}})
        _write_pkg(root / "desktop" / "node_modules" / "left-pad", {"dependencies": {"left-pad": "1"}})
        _write_pkg(root / "web" / "dist", {"dependencies": {"built": "1"}})
        _write_pkg(root / "tools", {"dependencies": {"zod": "3"}})
        _write_pkg(root / ".claude" / "worktrees" / "agent-x" / "desktop", {"dependencies": {"other-branch-only": "1"}})  # 다른 브랜치 사본
        names = github.dependencies_from_package_json(root)
        assert sorted(names) == ["@radix-ui/react-dialog", "electron", "react", "zod"], names
        assert names == github.dependencies_from_package_json(root)          # 순서가 안정적
        assert github.dependencies_from_package_json(root / "none") == []


def test_github_claude_md_skips_npm_rows():
    skipped = []
    names = github.dependencies_from_claude_md(DESKTOP_STACK, skipped)
    assert names == ["PyYAML", "litellm"], names
    assert len(skipped) == 1 and "Electron" in skipped[0], skipped
    assert github.dependencies_from_claude_md(DESKTOP_STACK) == ["PyYAML", "litellm"]   # skipped 없이도 동작


def test_github_repo_from_npm_repository_forms():
    f = github.repo_from_npm_repository
    for u in ["git+https://github.com/o/r.git", "https://github.com/o/r", "git://github.com/o/r.git",
              "git+ssh://git@github.com/o/r.git", "git@github.com:o/r.git", "github:o/r", "o/r",
              {"type": "git", "url": "git+https://github.com/o/r.git", "directory": "packages/x"},
              "https://github.com/o/r/tree/main/packages/x"]:
        assert f(u) == "o/r", u
    for u in [None, "", "https://gitlab.com/o/r.git", "git+https://bitbucket.org/o/r.git", {"type": "git"},
              "https://github.com/sponsors/o", 42]:
        assert f(u) is None, u


def test_github_npm_url_encoding_and_cache_key():
    assert github.npm_latest_url("react") == "https://registry.npmjs.org/react/latest"
    assert github.npm_latest_url("@radix-ui/react-dialog") == "https://registry.npmjs.org/@radix-ui%2Freact-dialog/latest"

    class F:
        def __init__(self):
            self.urls = []
        def get(self, url):
            self.urls.append(url)
            if "npmjs" in url:
                return 200, json.dumps({"repository": "facebook/react"}).encode()
            return 200, json.dumps({"info": {"project_urls": {"Source": "https://github.com/markfinger/python-react"}}}).encode()
    f, cache = F(), {}
    assert github.resolve_repo("react", {}, f, cache, "npm") == "facebook/react"
    assert github.resolve_repo("react", {}, f, cache) == "markfinger/python-react"      # npm:react != pypi:react
    assert github.resolve_repo("react", {}, f, cache, "npm") == "facebook/react"        # 캐시
    assert len(f.urls) == 2 and set(cache) == {"npm:react", "pypi:react"}, (f.urls, cache)


def test_github_resolve_override_before_npm():
    class NoNet:
        def get(self, url):
            raise AssertionError("override가 있으면 registry를 부르지 않는다")
    assert github.resolve_repo("Electron", {"electron": "electron/electron"}, NoNet(), {}, "npm") == "electron/electron"


def test_github_releases_mixes_pypi_and_npm_with_dedupe():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _write_pkg(root, {"dependencies": {"@radix-ui/react-dialog": "1", "@radix-ui/react-label": "2",
                                           "react": "19", "no-repo-pkg": "1"}})
        npm = {"@radix-ui%2Freact-dialog": "git+https://github.com/radix-ui/primitives.git",
               "@radix-ui%2Freact-label": {"url": "https://github.com/radix-ui/primitives", "directory": "x"},
               "react": "git+https://github.com/facebook/react.git"}

        class F:
            def __init__(self):
                self.rel = []
            def get(self, url):
                if "registry.npmjs.org/" in url:
                    key = url.split("registry.npmjs.org/")[1].removesuffix("/latest")
                    if key in npm:
                        return 200, json.dumps({"repository": npm[key]}).encode()
                    return 200, b"{}"
                return 200, json.dumps({"info": {"project_urls": {"Source": "https://github.com/pydantic/pydantic"}}}).encode()
            def get_json(self, url, headers=None):
                self.rel.append(url)
                return [_rel("v1", "2026-10-07T00:00:00Z")]
        f = F()
        rep = {"requests": 0, "failed": []}
        since = time.mktime((2026, 10, 1, 0, 0, 0, 0, 0, 0)) - time.timezone
        items = github.releases({"dependency_repos": {}}, f, {"me/app": DESKTOP_STACK.replace("litellm", "pydantic")},
                                rep, since, {}, {"me/app": root})
        repos = sorted(u.split("/repos/")[1].split("/releases")[0] for u in f.rel)
        assert repos == ["facebook/react", "pydantic/pydantic", "radix-ui/primitives"], repos   # 모노레포는 1번
        d = rep["dependencies"]
        assert d["by_ecosystem"] == {"pypi": 2, "npm": 4} and d["total"] == 6, d
        assert d["repos"] == 3 and d["skipped_npm_rows"] == 1, d
        assert {"target": "me/app", "dependency": "no-repo-pkg", "ecosystem": "npm"} in d["unresolved"], d
        assert {i.meta["ecosystem"] for i in items} == {"pypi", "npm"}
        assert all("dependency" in i.meta for i in items)


def test_github_releases_package_json_without_claude_md():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _write_pkg(root, {"dependencies": {"react": "19"}})

        class F:
            def get(self, url):
                return 200, json.dumps({"repository": "github:facebook/react"}).encode()
            def get_json(self, url, headers=None):
                return []
        rep = {"requests": 0, "failed": []}
        github.releases({}, F(), {}, rep, 0, {}, {"mate/front": root})
        assert rep["dependencies"]["repos"] == 1 and rep["dependencies"]["by_ecosystem"] == {"npm": 1}, rep


def _git_repo(root, tracked, untracked=None):
    """임시 폴더에 진짜 git init + add (커밋은 필요 없다 — ls-files는 인덱스를 본다). tracked/untracked: {package.json 상대 경로: 내용}."""
    import subprocess
    root = Path(root)
    for rel, obj in {**tracked, **(untracked or {})}.items():
        _write_pkg(root / Path(rel).parent, obj)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "--"] + list(tracked), check=True)


def test_stack_git_mode_ignores_untracked_manifests():
    import tempfile
    from collector import stack
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t, {"package.json": {"dependencies": {"react": "19"}}},
                  {"desktop/.dev-data-x/package.json": {"dependencies": {"next": "15"}}})
        paths, info = stack.manifest_files(t)
        assert info["mode"] == "git" and info["why"] is None, info
        assert [p.relative_to(t).as_posix() for p in paths] == ["package.json"], paths
        assert github.dependencies_from_package_json(t) == ["react"]


def test_stack_exclude_drops_tracked_files_and_reports_them():
    import tempfile
    from collector import stack
    tracked = {"app/package.json": {"dependencies": {"react": "19"}},
               "demo/profiles/a/package.json": {"dependencies": {"next": "15"}},
               "demo/other/package.json": {"dependencies": {"vue": "3"}}}
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t, tracked)
        assert github.dependencies_from_package_json(t) == ["react", "vue", "next"]
        for ex in (["demo/profiles"], ["demo/profiles/"], ["demo/profiles/a/package.json"], ["demo/profiles/*/package.json"]):
            paths, info = stack.manifest_files(t, ex)
            assert info["excluded"] == ["demo/profiles/a/package.json"], (ex, info)
            assert github.dependencies_from_package_json(t, ex) == ["react", "vue"], ex
        assert stack.manifest_files(t, ["demo/prof"])[1]["excluded"] == []          # 접두 문자열이 아니라 폴더 경계로 맞춘다
        e = stack.package_json_entries(t, ["demo/profiles"])
        assert [(x["name"], x["file"], x["section"]) for x in e] == [
            ("react", "app/package.json", "dependencies"), ("vue", "demo/other/package.json", "dependencies")], e


def test_stack_non_git_folder_walks_with_reason():
    import tempfile
    from collector import stack
    with tempfile.TemporaryDirectory() as t:
        _write_pkg(Path(t) / "a", {"dependencies": {"react": "19"}, "devDependencies": {"vite": "7"}})
        paths, info = stack.manifest_files(t)
        assert info["mode"] == "walk" and info["why"], info
        assert [x["section"] for x in stack.package_json_entries(t)] == ["dependencies", "devDependencies"]
        assert github.dependencies_from_package_json(t) == ["react", "vite"]
        paths, info = stack.manifest_files(Path(t) / "nope")
        assert paths == [] and info["why"], info


def test_stack_skips_claude_dir_in_both_modes():
    import tempfile
    from collector import stack
    with tempfile.TemporaryDirectory() as t:                # walk
        _write_pkg(Path(t), {"dependencies": {"react": "19"}})
        _write_pkg(Path(t) / ".claude" / "worktrees" / "x", {"dependencies": {"next": "15"}})
        assert stack.manifest_files(t)[1]["mode"] == "walk"
        assert github.dependencies_from_package_json(t) == ["react"]
    with tempfile.TemporaryDirectory() as t:                # git — 추적돼 있어도 .claude는 보지 않는다
        _git_repo(t, {"package.json": {"dependencies": {"react": "19"}}, ".claude/worktrees/x/package.json": {"dependencies": {"next": "15"}}})
        paths, info = stack.manifest_files(t)
        assert info["mode"] == "git" and [p.relative_to(t).as_posix() for p in paths] == ["package.json"], (paths, info)


def test_github_releases_honors_excludes_end_to_end():
    import tempfile
    tracked = {"package.json": {"dependencies": {"react": "19"}}, "demo/package.json": {"dependencies": {"next": "15"}}}
    with tempfile.TemporaryDirectory() as t:
        _git_repo(t, tracked, {"scratch/package.json": {"dependencies": {"vue": "3"}}})

        class F:
            def get(self, url):
                return 200, json.dumps({"repository": "github:acme/x"}).encode()
            def get_json(self, url, headers=None):
                return []
        rep = {"requests": 0, "failed": []}
        github.releases({}, F(), {}, rep, 0, {}, {"mate/front": Path(t)}, {"mate/front": ["demo"]})
        d = rep["dependencies"]
        assert d["total"] == 1 and d["by_ecosystem"] == {"npm": 1}, d             # react만 — next(제외)·vue(untracked)는 안 읽는다
        m = d["manifests"]["mate/front"]
        assert m["mode"] == "git" and m["excluded"] == ["demo/package.json"], m


def test_hf_parsers():
    ms = huggingface.parse_models([{"id": "fastino/GLiNER2.5-Decide", "trendingScore": 207, "likes": 10,
                                    "pipeline_tag": "token-classification", "createdAt": "2026-09-24T00:00:00Z"}],
                                   "task:token-classification")
    assert ms[0].id == "hf-model:fastino/GLiNER2.5-Decide" and ms[0].meta["pipeline_tag"] == "token-classification"
    assert ms[0].signals["rank:task:token-classification"] == 1
    ps = huggingface.parse_papers([{"title": "Raven", "paper": {"id": "2609.00001", "upvotes": 501, "summary": "abs"}}],
                                  "2026-09-30")
    assert ps[0].id == "hf-paper:2609.00001" and ps[0].text == "abs" and ps[0].signals["upvotes"] == 501


def test_vendor_rule_select():
    names = vendor.services_from_claude_md(FINEPRINT_STACK) + ["Amazon Bedrock Guardrails", "TypeSafe Jev"]
    picked, missing, excluded = vendor.select(names, VENDORS)
    assert set(picked) == {"anthropic", "langfuse", "aws-bedrock"}, picked
    assert missing == ["TypeSafe Jev"], missing                     # 매핑이 없으면 「경로 없음」
    assert {e["why"] for e in excluded} == {"js", "data"}, excluded  # 일부러 뺀 것은 「제외」


def test_vendor_rule_not_vendor_and_dedupe():
    # 실측 (2026-10-02): 로컬 서빙 모델과 이 프로젝트 자신이 「경로 없음」으로 섞여 나왔고, 같은 줄이 두 repo에서 두 번 나왔다
    cfg = {**VENDORS, "not_vendor": [{"match": ["localhost", "로컬 전용"], "why": "local"},
                                     {"match": ["decision radar"], "why": "self"}]}
    names = ["`Intel/Qwen3.5-4B` — vLLM localhost:8000", "`qwen3.5-4b` — llama-server, 로컬 전용",
             "Decision Radar 제품", "TypeSafe Jev", "TypeSafe Jev"]
    picked, missing, excluded = vendor.select(names, cfg)
    assert missing == ["TypeSafe Jev"], missing
    assert [e["why"] for e in excluded] == ["local", "local", "self"], excluded
    assert all(e["kind"] == "not_vendor" for e in excluded)


def test_page_hash_ignores_build_ids():
    # 같은 본문인데 빌드 ID만 다른 두 페이지 — 원본 해시는 다르고 텍스트 해시는 같아야 한다 (probe 2026-10-02)
    a = b'<html><script>window.__BUILD__="a1"</script><h2>Sep 30</h2><p>Sonnet 5.5 released</p></html>'
    b = b'<html><script>window.__BUILD__="b2"</script><h2>Sep 30</h2><p>Sonnet 5.5 released</p></html>'
    assert a != b
    pa = vendor.parse_page(a, "anthropic", "https://x", 0)
    pb = vendor.parse_page(b, "anthropic", "https://x", 0)
    assert pa.meta["text_hash"] == pb.meta["text_hash"]
    assert pa.text == "Sep 30\nSonnet 5.5 released"     # 블록마다 한 줄 — 페이지는 줄 단위로 비교한다 (HYO-72)


def test_page_text_lines_and_untruncated():
    body = ("<ul><li>Added <b>batch</b>&nbsp;API</li>\n  <li>Deprecated  x</li></ul><p>" + "z" * 9000 + "</p>").encode()
    it = vendor.parse_page(body, "anthropic", "https://x", 0)
    assert it.text.split("\n")[:2] == ["Added batch API", "Deprecated x"], it.text[:60]
    assert len(it.to_dict()["text"]) == it.meta["text_chars"] > 9000      # 페이지는 TEXT_MAX에서 자르지 않는다
    assert html_to_text("<p>a</p><p>b</p>") == "a b"                       # 피드 본문은 지금처럼 한 줄


def test_vendor_markdown_page_kept_verbatim():
    md = b"# Changelog\n\n<h2 id=\"v072\">v0.7.2 (2026-09-26)</h2>\n\n* add `http2` extra\n"
    it = vendor.parse_page(md, "typesafe", "https://docs.typesafe.ai/sdk/python/changelog.md", 0, "markdown")
    assert "v0.7.2 (2026-09-26)" in it.text and it.text.startswith("# Changelog")   # 태그를 지우지 않는다
    assert it.meta["format"] == "markdown"


def test_vendor_rss_include_filter():
    rss = b"""<rss><channel>
      <item><title>Amazon Bedrock adds X</title><link>https://aws/1</link><guid>1</guid><description>d</description></item>
      <item><title>DynamoDB export</title><link>https://aws/2</link><guid>2</guid><description>d</description></item>
    </channel></rss>"""
    got = vendor.parse_feed(rss, "aws-bedrock", "https://feed", include="(?i)bedrock")
    assert [g.title for g in got] == ["Amazon Bedrock adds X"]


def test_fetcher_min_interval():
    clock = [100.0]
    slept = []
    f = fetch.Fetcher(min_interval={"www.reddit.com": 61}, sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)),
                      now=lambda: clock[0])
    f._wait("www.reddit.com")
    clock[0] += 5
    f._wait("www.reddit.com")
    assert slept and abs(slept[0] - 56) < 1e-6, slept


def test_fetcher_retries_429(monkeypatch=None):
    import io
    import urllib.error
    import urllib.request
    calls = []

    class Resp(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=60):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {"Retry-After": "1"}, io.BytesIO(b""))
        return Resp(b"ok")

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        f = fetch.Fetcher(sleep=lambda s: None)
        st, body = f.get("https://www.reddit.com/r/x/top.rss")
    finally:
        urllib.request.urlopen = orig
    assert st == 200 and body == b"ok" and len(calls) == 2
    assert f.log[-1][2] == 2          # 두 번째 시도에 성공했다고 기록


def test_extract_links_skips_source_pages():
    got = extract_links('<a href="https://news.ycombinator.com/item?id=1">c</a> https://arxiv.org/abs/2609.1.')
    assert got == ["https://arxiv.org/abs/2609.1"], got


def test_item_text_truncated():
    it = Item(id="x", source="hn", channels=[], title="t", url="u", text="a" * 9000)
    assert len(it.to_dict()["text"]) == 8000


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:  # noqa: BLE001 — 예외는 전부 실패로 센다
            failed += 1
            print(f"FAIL {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

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


def test_github_dependencies_from_package_json():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        _write_pkg(root / "desktop", {"dependencies": {"react": "^19", "@radix-ui/react-dialog": "^1"},
                                      "devDependencies": {"electron": "^39", "react": "^19"}})
        _write_pkg(root / "desktop" / "node_modules" / "left-pad", {"dependencies": {"left-pad": "1"}})
        _write_pkg(root / "web" / "dist", {"dependencies": {"built": "1"}})
        _write_pkg(root / "tools", {"dependencies": {"zod": "3"}})
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

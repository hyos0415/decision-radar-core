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
    assert pa.text == "Sep 30 Sonnet 5.5 released"


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

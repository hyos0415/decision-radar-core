"""합집합(pipeline/union.py, HYO-167) 오프라인 테스트 — 네트워크 · 모델 없이 (가짜 fetcher · 작은 코퍼스).

  python pipeline/tests/test_union.py

재생 데이터 모양을 그대로 쓴다: 스킬 기간 초안 ADR 0015(Langfuse Python SDK v5.0.0) · 0016(Anthropic Python SDK v2.0.0)과
얼린 코퍼스의 실제 항목 id (W43: langfuse v5.0.1 · v5.0.2, anthropic v1.15.0 / W44: anthropic v2.0.0).
- 패키지 이름: 근거 링크의 GitHub 저장소 → 코퍼스 의존성 · 「Anthropic Python SDK」 → anthropic
- 같은 항목: v5.0.0 ↔ v5.0.1 · v5.0.2 = both (주 버전) · v2.0.0 ↔ v1.15.0 = 스킬만
- 원문: GitHub releases API 먼저, release 객체가 없으면 페이지 · 못 받으면 이유만 남기고 스킬 요약을 쓰지 않는다
- 정규화 항목은 detect_feed.feed_entry가 그대로 읽는다 (kind dependency_release · package · version)
- 기본 꺼짐: RADAR_UNION이 없으면 run은 아무것도 안 한다
뒤쪽 변이 검사(규율 3): 주 버전 대신 전체 버전으로 묶거나 · 저장소 → 의존성 해석을 빼거나 · 못 받은 원문 자리에 스킬 요약을 넣거나 ·
꺼짐 검사를 빼면 위 테스트가 빨개져야 한다.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import detect_feed, skill_line, union  # noqa: E402


def rel(repo, tag, dep):
    return {"id": f"github-release:{repo}@{tag}", "source": "github", "channels": ["dependency-releases"], "title": f"{repo} {tag}",
            "url": f"https://github.com/{repo}/releases/tag/{tag}", "text": f"{tag} notes", "meta": {"repo": repo, "tag": tag, "dependency": dep}}


W43 = [rel("langfuse/langfuse-python", "v5.0.1", "langfuse"), rel("langfuse/langfuse-python", "v5.0.2", "langfuse"),
       rel("anthropics/anthropic-sdk-python", "v1.15.0", "anthropic"), rel("pydantic/pydantic", "v2.14.2", "pydantic"),
       {"id": "hn:1", "source": "hn", "title": "Show HN: x", "url": "https://news.ycombinator.com/item?id=1", "text": "", "meta": {}}]
W44 = [rel("anthropics/anthropic-sdk-python", "v2.0.0", "anthropic"), rel("anthropics/anthropic-sdk-python", "v2.1.0", "anthropic")]

D15 = """---
status: proposed
technology: Langfuse
use: 의존성 릴리스 Python SDK v5.0.0 대응 (major·breaking)
affected:
- {repo: finance_verifier, component: verifier}
evidence:
- {url: langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5, source_type: official_doc}
- {url: github.com/langfuse/langfuse-python/releases/tag/v5.0.0, source_type: release_note}
- {url: "repo:finance_verifier/src/verifier/client.py", source_type: repo}
---
# Langfuse Python SDK v5.0.0 대응
"""
D16 = """---
status: proposed
technology: Anthropic Python SDK
use: 의존성 릴리스 v2.0.0 대응 (breaking)
affected:
- {repo: decision-radar, component: product}
evidence:
- {url: github.com/anthropics/anthropic-sdk-python/releases/tag/v2.0.0, source_type: release_note}
---
# Anthropic Python SDK v2.0.0 대응
"""


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, headers=None):
        self.calls.append(url)
        v = self.pages.get(url)
        if v is None:
            return 404, b""
        return 200, v.encode("utf-8") if isinstance(v, str) else v


PAGES = {"https://api.github.com/repos/anthropics/anthropic-sdk-python/releases/tags/v2.0.0":
         json.dumps({"name": "v2.0.0", "body": "### BREAKING CHANGES\n* api version 2026-10-21"}),
         # Langfuse v5.0.0은 release 객체가 없다 (API 404) → 태그 페이지
         "https://github.com/langfuse/langfuse-python/releases/tag/v5.0.0": "<html><body><h1>Release v5.0.0</h1><p>Breaking: drop v4 API</p></body></html>"}


def drafts(tmp):
    d = Path(tmp) / "adr"
    d.mkdir(parents=True, exist_ok=True)
    (d / "0015-langfuse-python-sdk-v5-breaking.md").write_text(D15, encoding="utf-8")
    (d / "0016-anthropic-python-sdk-v2-breaking.md").write_text(D16, encoding="utf-8")
    return union.replay_candidates(sorted(d.glob("*.md")))


def snapshot(tmp, items, name):
    d = Path(tmp) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "items.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")
    return d


def test_resolve_package():
    known = union.known_packages(W43)
    assert union.resolve_package({"technology": "Langfuse Python SDK", "urls": ["github.com/langfuse/langfuse-python/releases/tag/v5.0.0"]}, known) == "langfuse"
    assert union.resolve_package({"technology": "Anthropic Python SDK", "urls": []}, known) == "anthropic"
    assert union.resolve_package({"technology": "Pydantic", "urls": []}, known) == "pydantic"
    assert union.resolve_package({"technology": "x", "urls": ["https://pypi.org/project/uvicorn/0.40.0/"]}, known) == "uvicorn"
    assert union.resolve_package({"technology": "GPT-6.1 Terra", "urls": ["https://openai.com/index/terra"]}, known) is None


def test_primary_url_first_party_order():
    c = {"evidence": [{"url": "repo:fv/x.py", "source_type": "repo"}, {"url": "reddit.com/r/x", "source_type": "community"},
                      {"url": "docs.x.com/upgrade", "source_type": "official_doc"}, {"url": "github.com/a/b/releases/tag/v1.0.0", "source_type": "release_note"}]}
    assert union.primary_url(c) == "github.com/a/b/releases/tag/v1.0.0"
    assert union.primary_url({"evidence": [{"url": "repo:fv/x.py", "source_type": "repo"}], "urls": ["repo:fv/x.py"]}) is None


def test_replay_w43_both_and_skill_only():
    with tempfile.TemporaryDirectory() as tmp:
        cands = drafts(tmp)
        assert [c["version"] for c in cands] == ["5.0.0", "2.0.0"]
        f = FakeFetcher(PAGES)
        u = union.build(cands, Path(tmp) / "out", week="2026-W43", snapshot=snapshot(tmp, W43, "W43"), fetcher=f)
        assert u["both"] == {"skill:github.com/langfuse/langfuse-python/releases/tag/v5.0.0":
                             ["github-release:langfuse/langfuse-python@v5.0.1", "github-release:langfuse/langfuse-python@v5.0.2"]}, u["both"]
        assert u["skill_only"] == ["skill:github.com/anthropics/anthropic-sdk-python/releases/tag/v2.0.0"], u["skill_only"]
        assert u["counts"] == {"found": 2, "fetched": 2, "not_fetched": 0, "both": 1, "skill_only": 1}, u["counts"]
        assert json.loads((Path(tmp) / "out" / "union.json").read_text(encoding="utf-8"))["line"].startswith("스킬 줄 2")
        items = [json.loads(x) for x in (Path(tmp) / "out" / "items.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [i["meta"]["found_by"] for i in items] == ["both", "skill"]


def test_replay_w44_both():
    with tempfile.TemporaryDirectory() as tmp:
        u = union.build(drafts(tmp), Path(tmp) / "out", week="2026-W44", snapshot=snapshot(tmp, W44, "W44"), fetch=False)
        assert set(u["both"]) == {"skill:github.com/anthropics/anthropic-sdk-python/releases/tag/v2.0.0"} and len(u["skill_only"]) == 1, u
        assert u["both"]["skill:github.com/anthropics/anthropic-sdk-python/releases/tag/v2.0.0"] == [
            "github-release:anthropics/anthropic-sdk-python@v2.0.0", "github-release:anthropics/anthropic-sdk-python@v2.1.0"]


def test_fetch_api_first_then_page_and_failure_keeps_no_summary():
    with tempfile.TemporaryDirectory() as tmp:
        f = FakeFetcher(PAGES)
        items = union.freeze(drafts(tmp), Path(tmp) / "out", week="2026-W43", corpus=W43, fetcher=f)
        by = {i["meta"]["skill"]["adr"]: i for i in items}
        assert "BREAKING CHANGES" in by["0016"]["text"] and by["0016"]["meta"]["fetch"]["status"] == 200
        assert "drop v4 API" in by["0015"]["text"]  # API 404 → 태그 페이지
        assert f.calls[0].startswith("https://api.github.com/repos/langfuse/langfuse-python/releases/tags/v5.0.0")
        log = json.loads((Path(tmp) / "out" / "fetch.json").read_text(encoding="utf-8"))["urls"]
        assert {v["file"] for v in log.values()} == {"pages/S01.md", "pages/S02.md"}
        # 못 받으면 — 상태만 남기고 본문은 비운다 (스킬이 쓴 제목 · 요약을 본문에 넣지 않는다)
        items = union.freeze(drafts(tmp), Path(tmp) / "out2", week="2026-W43", corpus=W43, fetcher=FakeFetcher({}))
        assert all(i["text"] == "" and i["meta"]["fetch"]["status"] == 404 for i in items), [i["meta"]["fetch"] for i in items]
        assert json.loads((Path(tmp) / "out2" / "fetch.json").read_text(encoding="utf-8"))["urls"]


def test_normalized_item_reads_as_feed_entry():
    with tempfile.TemporaryDirectory() as tmp:
        items = union.freeze(drafts(tmp), Path(tmp) / "out", week="2026-W43", corpus=W43, fetcher=FakeFetcher(PAGES))
        e = detect_feed.feed_entry(items[1])
        assert e["kind"] == "dependency_release" and e["package"] == "anthropic" and e["version"] == "v2.0.0" and e["source_type"] == "release_note", e
        assert items[1]["id"].startswith("skill:") and items[1]["source"] == "skill"
        assert union.item_key(items[0]) == skill_line.item_key("langfuse", "5.0.1")


def test_junction_views():
    with tempfile.TemporaryDirectory() as tmp:
        items = union.freeze(drafts(tmp), Path(tmp) / "out", week="2026-W43", corpus=W43, fetch=False)
        u = union.merge(W43, items)
        merged = union.merged_items(W43, items, u)
        assert len(merged) == len(W43) + 1
        fb = {i["id"]: i["meta"]["found_by"] for i in merged}
        assert fb["github-release:langfuse/langfuse-python@v5.0.1"] == "both" and fb["hn:1"] == "collect"
        assert fb["skill:github.com/anthropics/anthropic-sdk-python/releases/tag/v2.0.0"] == "skill"
        assert "found_by" not in W43[0]["meta"]  # 원본 코퍼스는 그대로
        assert [i["id"] for i in union.skill_only(items, u)] == u["skill_only"]


def test_off_by_default():
    assert not union.enabled({}) and not union.enabled({"RADAR_UNION": "1"}) and union.enabled({"RADAR_UNION": "on"})
    saved = os.environ.pop("RADAR_UNION", None)
    called = []
    orig = union.run
    union.run = lambda *a, **k: called.append(1)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            assert union.main(["run", "--week", "2026-W46", "--out", tmp]) == 0 and not called
    finally:
        union.run = orig
        if saved is not None:
            os.environ["RADAR_UNION"] = saved


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig = {"major": skill_line.major, "resolve": union.resolve_package, "fetch_text": union.fetch_text, "enabled": union.enabled}

    def full_version(v):                      # 주 버전 대신 전체 버전
        return v

    def no_repo(cand, known):                 # 저장소 → 의존성 해석 없이 이름만
        return known["deps"].get(skill_line.norm(cand.get("technology") or ""))

    def summary_fallback(fetcher, key, headers=None):  # 못 받으면 스킬이 쓴 것으로 채움
        st, text = orig["fetch_text"](fetcher, key, headers)
        return st, text or "스킬 요약"

    return [("주 버전 대신 전체 버전", lambda: setattr(skill_line, "major", full_version)),
            ("저장소 → 의존성 해석 없음", lambda: setattr(union, "resolve_package", no_repo)),
            ("못 받은 원문에 스킬 요약", lambda: setattr(union, "fetch_text", summary_fallback)),
            ("꺼짐 검사 없음", lambda: setattr(union, "enabled", lambda env=None: True))], orig


def run_mutations():
    muts, orig = mutations()
    missed = 0
    for name, apply in muts:
        apply()
        failed = 0
        for _n, f in TESTS:
            try:
                f()
            except Exception:  # noqa: BLE001
                failed += 1
        skill_line.major, union.resolve_package, union.fetch_text, union.enabled = orig["major"], orig["resolve"], orig["fetch_text"], orig["enabled"]
        print(f"  {'잡힘' if failed else '놓침'} {name}")
        missed += not failed
    return missed, len(muts)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
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
    missed, n = run_mutations()
    print(f"변이 {n}개 중 {n - missed}개 잡힘")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())

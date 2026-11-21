"""레인 C 화제 오프라인 테스트 — 네트워크·LLM 없이 손으로 만든 항목만.

  python pipeline/tests/test_topics.py

뒤쪽에 변이 검사가 있다 (CLAUDE.md 규율 3): 코드를 일부러 망가뜨려서 위 테스트가 실제로 빨개지는지 본다.
잡아야 하는 것: 모음 글이 토픽을 잇기 · 크로스포스트 중복 · 링크 정규화 없음 · 자리 나눔 없이 출처 수만 · 백분위가 pool 크기 무시.
"""
import random
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline import topics  # noqa: E402

LIST_POST_LINKS = topics.MAX_LINKS + 2     # 가져올 때 고정 — 변이가 상수를 바꿔도 픽스처는 그대로


def item(iid, source, title="", url="", links=(), signals=None, channels=None, author=None, new=True):
    ch = channels or {"hn": ["stories"], "reddit": ["r/LocalLLaMA:top-week"], "github": ["new-repos"],
                      "huggingface": ["trending"]}[source]
    meta = {"author": author} if author else {}
    return {"id": iid, "source": source, "title": title or iid, "url": url, "links": list(links), "channels": ch,
            "signals": signals or {}, "meta": meta, "new": new}


def by_title(res):
    return {t["title"]: t for t in res["topics"]}


# ---------------- 링크 키 ----------------

def test_link_key_normalization():
    k = topics.link_key
    assert k("https://arxiv.org/abs/2610.07659") == k("https://arxiv.org/pdf/2610.07659v2") == k(
        "https://huggingface.co/papers/2610.07659") == "arxiv:2610.07659"
    assert k("https://github.com/Niko1221/Strata") == k("https://github.com/niko1221/strata/tree/main/docs") == "github:niko1221/strata"
    assert k("https://github.com/vllm-project/vllm/issues/1") != k("https://github.com/vllm-project/vllm")
    assert k("https://www.youtube.com/watch?v=abc&t=3") == k("https://youtu.be/abc") == "youtube:abc"
    assert k("https://youtube.com/watch?v=abc") != k("https://youtube.com/watch?v=xyz")
    assert k("https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo") == "hf:qwen/qwen-image-2.1-turbo"
    assert k("https://huggingface.co/datasets/a/b") == "hf:datasets/a/b"
    assert k("https://example.com/post?utm=1#x") == "example.com/post"
    for skip in ("https://news.ycombinator.com/item?id=1", "https://www.reddit.com/r/x/comments/1/y/", "https://i.redd.it/a.png",
                 "https://openai.com/", "http://127.0.0.1:8000/health", "http://localhost:3000/x", "https://github.com/niko1221",
                 "https://huggingface.co/", ""):
        assert k(skip) is None, skip


def test_unparseable_link_has_no_key_and_does_not_break_grouping():
    """W47 Reddit 글 본문의 난독화 링크 `https://before-gpu-engineering[dot`에서 urlparse가 ValueError(Invalid IPv6 URL)를 던져
    build_topics → 탈락 추적(trace.jsonl) 전체가 없어졌다 (#305). 그 링크만 키 없음 — 같은 글의 다른 링크로는 그대로 묶인다."""
    try:
        assert topics.link_key("https://before-gpu-engineering[dot") is None
        assert topics.link_key("http://[::1") is None
        res = topics.build_topics([
            item("reddit:1", "reddit", "Study group", "https://www.reddit.com/r/x/1", ["https://before-gpu-engineering[dot", "https://cactuscompute.com/blog/whistle"],
                 signals={"rank:r/LocalLLaMA:top-week": 3}),
            item("hn:1", "hn", "Whistle: Speech to Text", "https://cactuscompute.com/blog/whistle", signals={"points": 500}),
        ])
    except ValueError as e:        # 변이 검사는 AssertionError만 「잡힘」으로 센다
        raise AssertionError(f"깨진 링크 하나가 묶기 전체를 멈췄다: {e}") from e
    assert by_title(res)["Whistle: Speech to Text"]["n_items"] == 2


# ---------------- 묶기 ----------------

def test_same_link_groups_across_sources():
    res = topics.build_topics([
        item("hn:1", "hn", "Whistle: Speech to Text", "https://cactuscompute.com/blog/whistle", signals={"points": 500}),
        item("reddit:1", "reddit", "Whistle on reddit", "https://www.reddit.com/r/x/1", ["https://cactuscompute.com/blog/whistle/"],
             signals={"rank:r/LocalLLaMA:top-week": 3}),
        item("hn:2", "hn", "Unrelated", "https://other.com/a", signals={"points": 10}),
    ])
    t = by_title(res)["Whistle: Speech to Text"]
    assert t["n_sources"] == 2 and t["n_items"] == 2 and t["sources"] == {"hn": 1, "reddit": 1}
    assert by_title(res)["Unrelated"]["n_items"] == 1


def test_list_post_does_not_bridge():
    """링크가 MAX_LINKS개를 넘는 모음 글은 링크로 묶지 않는다 — 무관한 두 토픽이 이어지면 안 된다."""
    many = [f"https://site{i}.com/p" for i in range(LIST_POST_LINKS - 2)] + ["https://a.com/x", "https://b.com/y"]
    res = topics.build_topics([
        item("hn:a", "hn", "A", "https://a.com/x", signals={"points": 100}),
        item("hn:b", "hn", "B", "https://b.com/y", signals={"points": 90}),
        item("reddit:list", "reddit", "Weekly links", "https://www.reddit.com/r/x/2", many, signals={"rank:r/LocalLLaMA:top-week": 1}),
    ])
    t = by_title(res)
    assert t["A"]["n_items"] == 1 and t["B"]["n_items"] == 1 and t["Weekly links"]["n_items"] == 1
    assert res["counts"]["list_posts"] == 1


def test_crosspost_counted_once():
    res = topics.build_topics([
        item("reddit:x1", "reddit", "Aplomb 1 released", "https://www.reddit.com/r/a/1", author="/u/z",
             signals={"rank:r/LocalLLaMA:top-week": 2}),
        item("reddit:x2", "reddit", "Aplomb 1 released ", "https://www.reddit.com/r/b/1", author="/u/z",
             channels=["r/LLMDevs:top-week"], signals={"rank:r/LLMDevs:top-week": 5}),
        item("reddit:y", "reddit", "Aplomb 1 released", "https://www.reddit.com/r/c/1", author="/u/other",
             signals={"rank:r/LocalLLaMA:top-week": 9}),
    ])
    multi = [t for t in res["topics"] if t["title"].startswith("Aplomb")]
    assert len(multi) == 2                                     # 다른 글쓴이의 같은 제목은 묶지 않는다
    mine = [t for t in multi if any(r["id"] == "reddit:x1" for r in t["items"])][0]
    assert mine["n_items"] == 1 and mine["sources"] == {"reddit": 1}


def test_lane_a_and_signal_less_items_excluded():
    res = topics.build_topics([
        item("github-release:x@v1", "github", "x v1", "https://github.com/o/x/releases/tag/v1", channels=["dependency-releases"]),
        item("hn:x", "hn", "x v1 is out", "https://github.com/o/x/releases/tag/v1", signals={"points": 50}),
        item("hn:nosig", "hn", "no signal", "https://c.com/z"),
    ])
    ids = {r["id"] for t in res["topics"] for r in t["items"]}
    assert ids == {"hn:x"}
    assert res["counts"]["lane_b"] == 2 and res["counts"]["candidates"] == 1


def test_topic_id_stable_under_order():
    its = [item(f"hn:{i}", "hn", f"t{i}", f"https://s.com/{i % 3}", signals={"points": i}) for i in range(9)]
    a = topics.build_topics(its)
    shuffled = its[:]
    random.Random(7).shuffle(shuffled)
    b = topics.build_topics(shuffled)
    assert [t["id"] for t in a["topics"]] == [t["id"] for t in b["topics"]]


# ---------------- 인기 ----------------

def test_popularity_percentile_by_hand():
    its = [item(f"hn:{i}", "hn", signals={"points": p}) for i, p in enumerate([100, 50, 50, 10])]
    its += [item(f"r:{i}", "reddit", signals={"rank:r/LocalLLaMA:top-week": i + 1}) for i in range(2)]
    p = topics.popularity(its)
    assert p["hn:0"]["pop"] == round(1 - 0.5 / 4, 4) and p["hn:0"]["rank"] == 1
    assert p["hn:1"]["rank"] == p["hn:2"]["rank"] == 2          # 같은 값은 같은 순위
    assert p["hn:3"]["rank"] == 4
    assert p["r:0"]["pop"] == round(1 - 0.5 / 2, 4)
    assert p["hn:0"]["pop"] > p["r:0"]["pop"]                   # 4개 중 1위 > 2개 중 1위


def test_reddit_best_pool_wins():
    it = item("r:1", "reddit", signals={"rank:r/LocalLLaMA:top-week": 50, "rank:r/LocalLLaMA:flair:News": 1},
              channels=["r/LocalLLaMA:top-week", "r/LocalLLaMA:flair:News"])
    filler = [item(f"r:f{i}", "reddit", signals={"rank:r/LocalLLaMA:top-week": i}) for i in range(1, 50)]
    p = topics.popularity([it] + filler)
    assert p["r:1"]["pool"] == "reddit:r/LocalLLaMA:flair:News" and p["r:1"]["rank"] == 1


def test_signal_label():
    L = topics.signal_label
    assert L("hn=points", 1, 2123) == "HN 2,123점"
    assert L("github:new-repos=stars", 4, 9691) == "GitHub ★9,691"
    assert L("huggingface:daily-papers=upvotes", 1, 265) == "HF 논문 265표"
    assert L("reddit:r/LocalLLaMA:top-week", 3, None) == "r/LocalLLaMA 주간 3위"
    assert L("reddit:r/LocalLLaMA:flair:New Model", 7, None) == "r/LocalLLaMA New Model 7위"
    assert L("huggingface:trending", 1, None) == "HF 트렌딩 1위"
    assert L("huggingface:task:text-ranking", 2, None) == "HF text-ranking 2위"


# ---------------- 순위 (자리 나눔) ----------------

def _fake(tid, n_sources, pop):
    return {"id": tid, "n_sources": n_sources, "popularity": pop}


def test_slot_split():
    ts = [_fake(f"m{i}", 2, 0.5 - i / 100) for i in range(8)] + [_fake("m3src", 3, 0.1)]
    ts += [_fake(f"s{i}", 1, 0.99 - i / 100) for i in range(8)]
    out = topics.rank_topics(ts, 10)
    head = [t["id"] for t in out[:10]]
    assert head[:5] == ["m3src", "m0", "m1", "m2", "m3"]       # 출처 수 먼저, 그다음 인기
    assert head[5:] == ["s0", "s1", "s2", "s3", "s4"]
    assert [t["section"] for t in out[:10]] == ["multi"] * 5 + ["single"] * 5
    assert [t["rank"] for t in out] == list(range(1, len(ts) + 1))


def test_slot_fill_when_one_side_short():
    ts = [_fake("m0", 2, 0.5), _fake("m1", 2, 0.4)] + [_fake(f"s{i}", 1, 0.9 - i / 100) for i in range(12)]
    head = [t["id"] for t in topics.rank_topics(ts, 10)[:10]]
    assert head[:2] == ["m0", "m1"] and len([h for h in head if h.startswith("s")]) == 8
    ts = [_fake(f"m{i}", 2, 0.5 - i / 100) for i in range(12)] + [_fake("s0", 1, 0.9)]
    head = [t["id"] for t in topics.rank_topics(ts, 10)[:10]]
    assert head.count("s0") == 1 and len(head) == 10


# ---------------- 변이 검사 (규율 3) ----------------

class Mutation:
    def __init__(self, mod, name, value):
        self.mod, self.name, self.value = mod, name, value

    def __enter__(self):
        self.old = getattr(self.mod, self.name)
        setattr(self.mod, self.name, self.value)

    def __exit__(self, *exc):
        setattr(self.mod, self.name, self.old)


def _raw_key(url):
    from urllib.parse import urlparse
    p = urlparse(url or "")
    return (p.netloc + p.path) or None


def _sources_first(ts, k):
    out = sorted(ts, key=lambda t: (-t["n_sources"], -t["popularity"], t["id"]))
    for r, t in enumerate(out, 1):
        t["rank"], t["section"] = r, "multi" if t["n_sources"] > 1 else "single"
    return out


def _popularity_ignoring_size(items):
    best = _ORIG_POPULARITY(items)
    for v in best.values():
        v["pop"] = round(1 - (v["rank"] - 1) / v["size"], 4)
    return best


_ORIG_POPULARITY = topics.popularity
_ORIG_LINK_KEY = topics.link_key


def _unguarded_link_key(url):
    """깨진 링크를 거르지 않던 때 — urlparse가 먼저 던진다 (#305)."""
    from urllib.parse import urlparse
    if url:
        urlparse(url.strip())
    return _ORIG_LINK_KEY(url)


def mutations():
    return [
        ("모음 글도 링크로 묶음", [(topics, "MAX_LINKS", 999)], test_list_post_does_not_bridge),
        ("크로스포스트 키 없음", [(topics, "crosspost_key", lambda it: None)], test_crosspost_counted_once),
        ("링크 정규화 없음", [(topics, "link_key", _raw_key)], test_link_key_normalization),
        ("자리 나눔 없이 출처 수 먼저", [(topics, "rank_topics", _sources_first)], test_slot_split),
        ("백분위가 pool 크기 무시", [(topics, "popularity", _popularity_ignoring_size)], test_popularity_percentile_by_hand),
        ("깨진 링크에서 던짐 (W47 trace.jsonl 없어짐)", [(topics, "link_key", _unguarded_link_key)], test_unparseable_link_has_no_key_and_does_not_break_grouping),
    ]


def run_mutations():
    failed = 0
    for name, patches, test in mutations():
        ctxs = [Mutation(m, n, v) for m, n, v in patches]
        for c in ctxs:
            c.__enter__()
        try:
            test()
            caught = False
        except AssertionError:
            caught = True
        finally:
            for c in reversed(ctxs):
                c.__exit__()
        print(f"  {'잡힘' if caught else '놓침 !!'}  변이: {name}  ({test.__name__})")
        failed += 0 if caught else 1
    return failed


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"  ok   {n}")
        except Exception:  # noqa: BLE001
            bad += 1
            print(f"  FAIL {n}")
            traceback.print_exc()
    print(f"{len(tests) - bad}/{len(tests)} 통과")
    if bad:
        return 1
    print("변이 검사 — 망가뜨린 코드를 테스트가 잡아야 한다")
    missed = run_mutations()
    print(f"변이 {len(mutations())}개 중 {len(mutations()) - missed}개 잡힘")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())

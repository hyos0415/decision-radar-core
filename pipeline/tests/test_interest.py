"""관심사 질의(pipeline/interest.py, HYO-187) 오프라인 테스트 — 모델 없이 (stub · 가짜 claude CLI).

  python pipeline/tests/test_interest.py

- 1단계 BM25(파이썬)는 별칭이 그대로 나오는 글을 위로 올리고, 복수형을 묶고, 긴 글의 뒤쪽 조각에서도 찾는다
- 상위 60까지 판정에 넘긴다 (사용자 11/12)
- 판정은 모델 → 스크립트가 번호 · 등급을 검사 · 어긋나면 그 관심사만 실패로 적고 나머지는 간다
- 레인 A(릴리스 · 벤더) 항목은 후보가 아니다 · 등급 2만 picks
- 구독으로만: 가짜 CLI가 API 키를 보면 실패한다
뒤쪽 변이 검사(규율 3): API 키를 안 지우거나 · 답을 검사 없이 믿거나 · 상위 30으로 줄이거나 · 어미를 안 떼거나 · 첫 조각만 보거나 ·
등급 1도 picks에 넣으면 위 테스트가 빨개져야 한다.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import interest  # noqa: E402

PROFILE = {"interests": [
    {"id": "hybrid-sparse-retrieval", "topic": "하이브리드·희소 검색", "description": "키워드와 벡터 검색을 합치는 방법",
     "aliases": ["hybrid search", "sparse retrieval", "rank fusion"], "examples": ["SPLADE", "RRF"], "not": ["SEO"], "active": True},
    {"id": "embeddings-rerankers", "topic": "임베딩·리랭커 모델", "description": "문서 검색용 임베딩과 재순위 모델",
     "aliases": ["embedding model", "reranker"], "examples": ["BGE-M3"], "not": [], "active": True},
    {"id": "speech", "topic": "음성", "aliases": ["speech-to-text"], "examples": [], "not": [], "active": False},
], "excludes": [{"topic": "이미지·영상 생성 도구"}]}


def _it(i, title, text="", source="reddit", channels=(), meta=None):
    return {"id": i, "source": source, "title": title, "url": f"https://example.com/{i}", "text": text,
            "channels": list(channels), "signals": {}, "meta": meta or {}}


FILLER = " ".join(f"word{n}" for n in range(600))
ITEMS = [
    _it("hf:splade", "SPLADE-v4: learned sparse retrieval model released", "A new sparse retrieval model with rank fusion support."),
    _it("rd:rerank", "New multilingual rerankers beat cross encoders", "We release two rerankers and an embedding model."),
    _it("rd:late", "A long post about my weekend", FILLER + " and finally hybrid search with RRF worked."),   # 뒤쪽 조각에만 낱말
    _it("rd:noise", "What GPU should I buy", "Thinking about a 4090 for local inference."),
    _it("gh:release", "langchain 1.2.0 hybrid search reranker", "release notes", source="github",
        channels=["dependency-releases"], meta={"dependency": "langchain"}),                                       # 레인 A — 후보 아님
]


def test_bm25_ranks_named_posts_stems_plurals_and_reads_later_chunks():
    idx = interest.BM25([x for x in ITEMS if not x["channels"]])
    hy = [d for d, _ in idx.search(interest.query_text(PROFILE["interests"][0]), 10)]
    assert hy[0] == "hf:splade", hy
    assert "rd:late" in hy, "긴 글의 뒤쪽 조각에 있는 낱말도 찾아야 한다"
    assert "rd:noise" not in hy
    em = [d for d, _ in idx.search(interest.query_text(PROFILE["interests"][1]), 10)]
    assert em[0] == "rd:rerank", em            # rerankers(복수) ↔ reranker
    assert idx.search("the and of", 10) == [], "불용어만 있는 질의는 아무것도 못 찾는다"
    plural = interest.BM25([_it("a", "Rerankers galore"), _it("b", "Something else entirely")])
    assert [d for d, _ in plural.search("reranker", 5)] == ["a"], "복수형(rerankers)이 단수 질의(reranker)에 걸려야 한다"


def test_query_text_uses_topic_en_then_aliases_examples():
    it = PROFILE["interests"][0]
    assert interest.query_text(it) == "Hybrid and sparse retrieval: hybrid search, sparse retrieval, rank fusion. Examples: SPLADE, RRF."
    assert interest.query_text({**it, "topic_en": "Sparse"}).startswith("Sparse: ")
    assert interest.query_text({"id": "new-one", "aliases": ["foo bar"], "examples": []}) == "foo bar."


def test_check_labels_catches_bad_answers():
    ok, p = interest.check_labels({"labels": [{"n": 1, "grade": 2, "why": ""}, {"n": 2, "grade": 0, "why": ""}]}, 2)
    assert not p and set(ok) == {1, 2}
    for bad in ([{"n": 1, "grade": 2, "why": ""}],                                                         # 빠짐
                [{"n": 1, "grade": 2, "why": ""}, {"n": 1, "grade": 0, "why": ""}, {"n": 2, "grade": 0, "why": ""}],   # 겹침
                [{"n": 1, "grade": 5, "why": ""}, {"n": 2, "grade": 0, "why": ""}]):                         # 등급
        assert interest.check_labels({"labels": bad}, 2)[1], bad


def test_run_stub_picks_grade2_in_rank_order_and_skips_lane_a_and_inactive():
    res = interest.run(ITEMS, PROFILE, engine="stub")
    assert res["lane_b"] == 4 and set(res["interests"]) == {"hybrid-sparse-retrieval", "embeddings-rerankers"}, res["interests"].keys()
    hy = res["interests"]["hybrid-sparse-retrieval"]
    allx = hy["picks"] + hy["topical"]
    assert all(x["id"] != "gh:release" for v in res["interests"].values() for x in v["picks"] + v["topical"])
    assert [x["rank"] for x in hy["picks"]] == sorted(x["rank"] for x in hy["picks"])
    assert all(x["grade"] == 2 for x in hy["picks"]) and all(x["grade"] == 1 for x in hy["topical"])
    assert hy["picks"] and hy["topical"], "stub은 2 · 1 · 0을 다 낸다 — 둘 다 있어야 칸이 섞이는지 볼 수 있다"
    assert hy["searched"] >= len(allx)


def test_top60_reaches_the_judge():
    many = [_it(f"rd:{n:03d}", f"hybrid search post {n}", "sparse retrieval " * (1 + n % 5)) for n in range(80)]
    res = interest.run(many, {"interests": [PROFILE["interests"][0]], "excludes": []}, engine="stub")
    hy = res["interests"]["hybrid-sparse-retrieval"]
    assert hy["searched"] == 60 and len(hy["picks"]) == 20 and len(hy["topical"]) == 20, (hy["searched"], len(hy["picks"]))   # stub: 번호 % 3


def _fake_cli(d, mode="ok"):
    p = Path(d) / "fake_claude.py"
    p.write_text("import json, os, re, sys\n"
                 "user = sys.stdin.read()\n"
                 "if os.environ.get('ANTHROPIC_API_KEY'):\n"
                 "    print(json.dumps({'is_error': True, 'subtype': 'api-key-seen', 'result': 'API key billing'})); sys.exit(0)\n"
                 f"mode = {mode!r}\n"
                 "m = re.search(r'후보 (\\d+)개', user)\n"
                 "if m:\n"                                                    # 3단계 — 주 10건 고르기
                 "    n = int(m.group(1))\n"
                 "    picks = [{'c': f'c{k}', 'reason': 'fake'} for k in range(n, 0, -1)] + [{'c': 'c1', 'reason': 'dup'}]\n"
                 "    if mode == 'badpick': picks = [{'c': 'zzz', 'reason': 'x'}]\n"
                 "    print(json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': {'picks': picks}})); sys.exit(0)\n"
                 "n = int(re.search(r'글 (\\d+)개', user).group(1))\n"
                 "labels = [{'n': k, 'grade': 2, 'why': 'fake'} for k in range(1, n + 1)]\n"
                 "if mode == 'short': labels = labels[:-1]\n"
                 "print(json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': {'labels': labels}, 'total_cost_usd': 0.1}))\n",
                 encoding="utf-8")
    return str(p)


def _with_env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        os.environ[k] = v

    def undo():
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    return undo


def test_claude_engine_subscription_only_raw_saved_and_reuse():
    with tempfile.TemporaryDirectory() as d:
        undo = _with_env(RADAR_CLAUDE_BIN=_fake_cli(d), ANTHROPIC_API_KEY="sk-test")
        try:
            res = interest.run(ITEMS, PROFILE, engine="claude", out=d)
        finally:
            undo()
        assert not res["failed"], res["failed"]
        assert all(x["grade"] == 2 for v in res["interests"].values() for x in v["picks"]) and res["counts"]["picks"] > 0
        raw = json.loads((Path(d) / "interest-raw.json").read_text(encoding="utf-8"))
        assert set(raw) - {"_pick"} == set(res["interests"]) and res["pick"]["engine"] == "claude"
        undo = _with_env(RADAR_CLAUDE_BIN=str(Path(d) / "no-such-cli.py"))
        try:
            again = interest.run(ITEMS, PROFILE, engine="claude", out=d, reuse=True)     # 저장한 판정을 쓴다 (CLI를 안 부른다)
        finally:
            undo()
        assert again["counts"]["picks"] == res["counts"]["picks"] and not again["failed"]
        assert again["to_detect"] == res["to_detect"] and again["pick"].get("reused"), "주 10건 고르기도 저장한 답을 쓴다"


def test_bad_answer_fails_that_interest_only():
    with tempfile.TemporaryDirectory() as d:
        undo = _with_env(RADAR_CLAUDE_BIN=_fake_cli(d, "short"))
        try:
            res = interest.run(ITEMS, PROFILE, engine="claude", out=d)
        finally:
            undo()
        assert set(res["failed"]) == {"hybrid-sparse-retrieval", "embeddings-rerankers"}, res["failed"]
        assert res["interests"] == {} and "빠진" in res["failed"]["hybrid-sparse-retrieval"]


MANY = {"interests": [
    {"id": f"i{k}", "topic": f"관심사 {k}", "aliases": [f"topic{k}"], "examples": [], "not": [], "active": True,
     "priority": "low" if k < 3 else "high" if k > 9 else "normal"} for k in range(14)], "excludes": []}
MANY_ITEMS = [_it(f"rd:{k}-{n}", f"topic{k} release {n}", f"topic{k} new tool number {n}") for k in range(14) for n in range(3)]


def test_week_pick_caps_at_10_checks_ids_and_dedupes():
    with tempfile.TemporaryDirectory() as d:
        undo = _with_env(RADAR_CLAUDE_BIN=_fake_cli(d))
        try:
            res = interest.run(MANY_ITEMS, MANY, engine="claude", out=d)
        finally:
            undo()
    ids = [p["id"] for p in res["to_detect"]]
    assert len(ids) == interest.PICK_N == 10 and len(set(ids)) == 10, ids          # 가짜 모델은 42개 + 겹친 번호를 낸다
    assert res["pick"]["engine"] == "claude" and res["pick"]["dropped"] >= 1
    assert [p["order"] for p in res["to_detect"]] == list(range(1, 11)) and all(p["topic"] for p in res["to_detect"])
    same = interest.pick_candidates({"a": {"picks": [{"id": "x1", "title": "Tool X released!", "rank": 2}]},
                                     "b": {"picks": [{"id": "x2", "title": "tool x released", "rank": 1}]}})
    assert [c["id"] for c in same] == ["x2"], "같은 제목은 검색 순위가 높은 글 하나만"


def test_week_pick_falls_back_to_rule_by_priority():
    with tempfile.TemporaryDirectory() as d:
        undo = _with_env(RADAR_CLAUDE_BIN=_fake_cli(d, "badpick"))
        try:
            res = interest.run(MANY_ITEMS, MANY, engine="claude", out=d)
        finally:
            undo()
    assert res["pick"]["engine"] == "fallback" and len(res["to_detect"]) == 10
    got = [p["interest"] for p in res["to_detect"]]
    assert len(set(got)) == 10, "규칙은 관심사마다 1건"
    assert {"i10", "i11", "i12", "i13"} <= set(got) and not {"i0", "i1", "i2"} & set(got), got     # high 먼저, low는 밀린다


def test_detect_feed_adds_week_picks_as_new_technology():
    from pipeline import detect_feed
    with tempfile.TemporaryDirectory() as d:
        snap, run, out = Path(d) / "snap", Path(d) / "run", Path(d) / "out"
        snap.mkdir(), run.mkdir()
        items = MANY_ITEMS + [_it("hf:model", "topic13 model card", "weights", source="huggingface")]
        (snap / "items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
        (run / "lane_a.json").write_text("[]", encoding="utf-8")
        (run / "run.json").write_text(json.dumps({"snapshot": str(snap), "counts": {"items": len(items), "lane_a": 0},
                                                  "lane_a_gate": {"model": "stub", "mode": "topk", "total_kept": 0, "label": "0", "listed": []}}),
                                      encoding="utf-8")
        res = interest.run(items, MANY, engine="stub")
        (run / "interest.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        f = detect_feed.build(run, out)
        feed = json.loads((out / "feed.json").read_text(encoding="utf-8"))
        assert [e["id"] for e in feed] == [p["id"] for p in res["to_detect"]] and len(feed) == 10
        assert all(e["kind"] == "new_technology" for e in feed)
        assert {e["source_type"] for e in feed if e["id"].startswith("rd:")} == {"community"}
        assert f["interest"]["to_detect"] == 10 and "관심사 질의" in detect_feed.funnel_line(f)
        why = json.loads((out / "gate.json").read_text(encoding="utf-8"))
        assert all("관심사" in v["reason"] for v in why.values())
        empty = Path(d) / "out2"
        (run / "interest.json").unlink()
        assert detect_feed.build(run, empty)["to_detect"] == 0, "interest.json이 없으면 예전 그대로"


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_check, orig_chunks, orig_stem, orig_run = interest.check_labels, interest.chunks_of, interest.stem, interest.run
    orig_picks, orig_choose_week = interest.check_picks, interest.choose_week
    from pipeline import detect_feed
    orig_choose = detect_feed.choose

    def no_cap(so, cands, n=10):
        out, _ = orig_picks(so, cands, 10 ** 6)
        return out, 0

    def no_fallback(per, profile, engine, model, old=None, n=10):
        picks, info = orig_choose_week(per, profile, engine, model, old, n)
        return ([], info) if info.get("engine") == "fallback" else (picks, info)

    def no_interest(lane_a, slots, all_kept=False, interest=None):
        return orig_choose(lane_a, slots, all_kept)

    def trust(so, n):
        return {x["n"]: x for x in so["labels"]}, []

    def first_chunk_only(item):
        return orig_chunks(item)[:1]

    def picks_with_grade1(*a, **k):
        res = orig_run(*a, **k)
        for v in res["interests"].values():
            v["picks"] = sorted(v["picks"] + v["topical"], key=lambda x: x["rank"])
        return res

    return [
        ("API 키를 안 지움", lambda: setattr(interest, "STRIP_ENV", ()), lambda: setattr(interest, "STRIP_ENV", ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))),
        ("답을 검사 없이 믿음", lambda: setattr(interest, "check_labels", trust), lambda: setattr(interest, "check_labels", orig_check)),
        ("상위 30으로", lambda: setattr(interest, "TOPK", 30) or setattr(interest.run, "__defaults__", ("claude", interest.DEFAULT_MODEL, None, False, 30)),
         lambda: setattr(interest, "TOPK", 60) or setattr(interest.run, "__defaults__", ("claude", interest.DEFAULT_MODEL, None, False, 60))),
        ("어미를 안 뗌", lambda: setattr(interest, "stem", lambda t: t), lambda: setattr(interest, "stem", orig_stem)),
        ("첫 조각만 봄", lambda: setattr(interest, "chunks_of", first_chunk_only), lambda: setattr(interest, "chunks_of", orig_chunks)),
        ("등급 1도 picks에", lambda: setattr(interest, "run", picks_with_grade1), lambda: setattr(interest, "run", orig_run)),
        ("주 10건 상한 없음", lambda: setattr(interest, "check_picks", no_cap), lambda: setattr(interest, "check_picks", orig_picks)),
        ("모델 실패 시 대신 고르지 않음", lambda: setattr(interest, "choose_week", no_fallback), lambda: setattr(interest, "choose_week", orig_choose_week)),
        ("detect에 관심사 10건을 안 넣음", lambda: setattr(detect_feed, "choose", no_interest), lambda: setattr(detect_feed, "choose", orig_choose)),
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

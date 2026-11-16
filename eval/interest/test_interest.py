"""관심사 질의 실험 스크립트 회귀 (HYO-187) — 판정 묶음 · 답 검사 · 채점이 잡아야 할 것을 잡는지.

  python eval/interest/test_interest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_interest as ri  # noqa: E402


def test_batches_cover_pool_once_and_split_evenly():
    pool = {"a": [f"d{i}" for i in range(250)], "b": ["x", "y"]}
    bs = ri.judge_batches(pool, None)
    a = [x for x in bs if x[0] == "a"]
    sizes = [len(x[2]) for x in a]
    assert len(a) == 3 and max(sizes) <= ri.BATCH_MAX and max(sizes) - min(sizes) <= 2, sizes
    assert sorted(d for x in a for d in x[2]) == sorted(pool["a"]), "풀 문서가 빠지거나 겹쳤다"
    assert ri.judge_batches(pool, None) == bs, "시드가 같으면 묶음이 같아야 한다"


def test_check_labels_catches_missing_duplicate_and_bad_grade():
    ok, p = ri.check_labels({"labels": [{"n": 1, "grade": 2, "why": ""}, {"n": 2, "grade": 0, "why": ""}]}, 2)
    assert not p and set(ok) == {1, 2}
    _, p = ri.check_labels({"labels": [{"n": 1, "grade": 2, "why": ""}]}, 2)
    assert any("빠진" in x for x in p)
    _, p = ri.check_labels({"labels": [{"n": 1, "grade": 1, "why": ""}, {"n": 1, "grade": 0, "why": ""}, {"n": 2, "grade": 0, "why": ""}]}, 2)
    assert any("겹친" in x for x in p)
    _, p = ri.check_labels({"labels": [{"n": 1, "grade": 3, "why": ""}, {"n": 2, "grade": 0, "why": ""}]}, 2)
    assert any("등급" in x for x in p)
    _, p = ri.check_labels({"labels": [{"n": 9, "grade": 0, "why": ""}, {"n": 1, "grade": 0, "why": ""}, {"n": 2, "grade": 0, "why": ""}]}, 2)
    assert any("모르는" in x for x in p)


def test_metrics_recall_counts_grade1_and_unjudged_as_miss():
    grades = {"a": 2, "b": 1, "c": 0, "z": 1}                    # z는 순위에 없다
    m = ri.per_query_metrics([["a", 1], ["c", 0.9], ["b", 0.8], ["u", 0.7]], grades)
    assert abs(m["recall"] - 2 / 3) < 1e-9, m                     # a · b 찾음, z 놓침
    assert abs(m["judged"] - 3 / 4) < 1e-9, m                     # u는 미판정
    m2 = ri.per_query_metrics([["a", 1], ["c", 0.9], ["b", 0.8]], grades, min_grade=2)
    assert m2["recall"] == 1.0
    assert ri.per_query_metrics([["a", 1]], {"a": 0})["recall"] is None, "정답이 없는 관심사는 평균에서 빠져야 한다"
    perfect = ri.per_query_metrics([["a", 1], ["b", 1], ["z", 1]], grades)
    assert abs(perfect["ndcg10"] - 1.0) < 1e-9


def test_review_set_takes_unique_relevant_and_zero_sample():
    pool = {"i": ["a", "b", "c"] + [f"z{n}" for n in range(40)]}
    runs = {"s4": {"i@en": [["a", 1], ["b", 1]] + [[f"z{n}", 1] for n in range(20)]},
            "s2": {"i@en": [["b", 1], ["c", 1]] + [[f"z{n}", 1] for n in range(20, 40)]},
            "kw": {}, "s3a.ko": {}, "s6": {}}
    labels = {"i": {"a": {"grade": 2}, "b": {"grade": 1}, "c": {"grade": 0}, **{f"z{n}": {"grade": 0} for n in range(40)}}}
    rows = ri.review_set(pool, runs, labels)
    uniq = [r["doc_id"] for r in rows if r["kind"] == "unique"]
    assert uniq == ["a"], uniq                                    # b는 두 전략이 찾아 승부를 가르지 않는다
    zeros = [r for r in rows if r["kind"] == "zero-sample"]
    assert len(zeros) == ri.ZERO_SAMPLE and len({r["doc_id"] for r in zeros}) == ri.ZERO_SAMPLE
    assert ri.review_set(pool, runs, labels) == rows, "시드가 같으면 표본이 같아야 한다"


def test_pool2_merges_weeks_adds_sentence_semantic_and_skips_labeled():
    run = lambda *ds: {"i@en": [[d, 1] for d in ds], "i@sent": [], "i@ko": [["k", 1]]}
    runs = {"W1": {"s2": run("a", "b"), "s4": {"i@en": [["c", 1]], "i@sent": [["s", 1]], "i@ko": [["k2", 1]]}},
            "W2": {"s2": run("b", "d")}}
    pool = ri.pool2(runs, labeled={"i": {"d": {"grade": 1}}})
    assert pool == {"i": [["W1", "a"], ["W1", "b"], ["W1", "c"], ["W1", "s"]]}, pool   # b는 처음 주 · d는 이미 판정 · 한국어는 풀 밖
    p, week_of = ri.split_pool(pool)
    assert p == {"i": ["a", "b", "c", "s"]} and week_of["s"] == "W1"
    assert ri.split_pool({"i": ["x"]}) == ({"i": ["x"]}, {})


def test_review_sample_is_20_positive_10_zero_and_stable():
    labels = {"i": {f"p{n}": {"grade": 1 + n % 2} for n in range(50)}, "j": {f"z{n}": {"grade": 0} for n in range(50)}}
    s = ri.review_sample(labels)
    assert len(s) == 30 and len(set(s)) == 30
    assert sum(1 for i, _ in s if i == "i") == ri.REVIEW_POS and sum(1 for i, _ in s if i == "j") == ri.REVIEW_NEG
    assert ri.review_sample(labels) == s


def test_cluster_ci_resamples_interests():
    ci = ri.cluster_ci({"a": [0.1, 0.1], "b": [0.1], "c": []})
    assert ci == [0.1, 0.1], ci
    lo, hi = ri.cluster_ci({"a": [1.0, 1.0, 1.0], "b": [-1.0]})
    assert lo < 0 < hi


def test_query_text_is_mechanical():
    it = {"id": "graph-rag", "topic": "그래프 기반 검색 (GraphRAG)", "aliases": ["GraphRAG", "knowledge graph"], "examples": []}
    assert ri.query_text(it, "en") == "Graph-based retrieval (GraphRAG): GraphRAG, knowledge graph."
    assert ri.query_text(it, "ko") == "그래프 기반 검색 (GraphRAG)"
    assert ri.query_text(it, "sent") == ri.DESC_EN["graph-rag"]
    assert set(ri.DESC_EN) == set(ri.TOPIC_EN)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"{n}/{n} 통과")

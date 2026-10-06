"""프로필 필터 오프라인 테스트 — 네트워크·LLM 없이 로컬 픽스처만.

  python pipeline/tests/test_profile_filter.py

뒤쪽에 변이 검사가 있다 (CLAUDE.md 규율 3): 코드를 일부러 망가뜨려서 위 테스트가 실제로 빨개지는지 본다.
통과는 증거가 아니다 — 감점 무시 · aliases를 kw에도 사용 · 레인 중복 · 부분 일치 · 순서 노출 · nDCG 할인 오류 · 알려진 정답 확대를 잡아야 한다.
"""
import csv
import datetime as dt
import json
import math
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline import interest_profile as ip  # noqa: E402
from pipeline import judging, keyword_match, lanes, profile_filter  # noqa: E402

TODAY = dt.date(2026, 10, 6)

PROFILE_YAML = """
version: 3
interests:
  - id: hybrid-sparse
    topic: 하이브리드 검색 (RRF)
    description: 키워드와 벡터를 합친다
    examples: [SPLADE, BGE-M3]
    not: [web search API]
    aliases: [hybrid search, rank fusion]
    priority: high
    added: 2026-10-02
  - id: multimodal
    topic: 멀티모달
    description: 이미지와 문서를 함께 이해
    examples: [TeleOCR]
    not: [이미지 생성]
    aliases: [OCR, VLM]
  - id: speech
    topic: 음성
    description: STT와 TTS
    aliases: [TTS]
    priority: low
  - id: retired
    topic: 은퇴한 관심사 Zebra
    description: 꺼 둔 것
    examples: [ZebraNet]
    active: false
excludes:
  - topic: image generation tool
    why: 스택 밖
"""


def profile():
    data = __import__("yaml").safe_load(PROFILE_YAML)
    p, errors, _ = ip.parse_profile(data, TODAY)
    assert not errors, errors
    return p


def it(iid, title, text="", url="", channels=("stories",), meta=None, new=True):
    return {"id": iid, "source": iid.split(":")[0], "channels": list(channels), "title": title, "url": url or f"https://x/{iid}",
            "created_at": "", "text": text, "signals": {}, "links": [], "meta": meta or {}, "new": new}


B_ITEMS = [
    it("hn:1", "SPLADE v3 released"),
    it("hn:2", "New RRF fusion technique", "We compare hybrid search variants"),
    it("hn:3", "A hybrid search proxy built on a web search API"),
    it("hn:4", "TeleOCR image generation tool"),
    it("hn:5", "Pure image generation tool news"),
    it("hn:6", "멀티모달 모델 이미지 생성 데모"),
    it("hn:7", "Multimodal OCR benchmarks"),
    it("hn:8", "TTS voice cloning"),
    it("hn:9", "Zebra ZebraNet released"),         # 꺼 둔 관심사만 걸린다 → 순위에 없어야 한다
    it("hn:10", "Gardening tips"),
]
A_ITEMS = [
    it("github-release:pydantic/pydantic@v3.0.0", "pydantic v3.0.0", channels=("dependency-releases",),
       meta={"repo": "pydantic/pydantic", "dependency": "pydantic"}),
    it("vendor:openai:1", "OpenAI adds a hybrid search tool", channels=("vendor:openai",), meta={"vendor": "openai"}),
    it("vendor-diff:anthropic:u@2026-W41", "[anthropic] page 변경", "Claude 3 retired", channels=("vendor:anthropic",),
       meta={"vendor": "anthropic", "kind": "page-diff"}),
]
PAGE = it("vendor-page:openai:u", "[openai] page", channels=("vendor:openai",), meta={"vendor": "openai", "kind": "page"})
ALL = B_ITEMS + A_ITEMS + [PAGE]


def by_id(ranked):
    return {e["id"]: e for e in ranked}


# ---------------- 프로필 ----------------

def test_profile_valid_defaults():
    p = profile()
    h, m, s, r = p["interests"]
    assert p["version"] == 3 and h["id"] == "hybrid-sparse"
    assert m["priority"] == "normal" and m["active"] is True and r["active"] is False
    assert m["added"] == "2026-10-06" and m["review_after"] == "2026-11-03"          # added 기본 오늘, review_after = added+28일
    assert h["added"] == "2026-10-02" and h["review_after"] == "2026-10-30"
    assert p["excludes"][0]["topic"] == "image generation tool"


def test_profile_auto_id_slug():
    data = {"version": 1, "interests": [{"topic": "GraphRAG", "description": "d"}, {"topic": "그래프 검색", "description": "d"}]}
    p, errors, _ = ip.parse_profile(data, TODAY)
    assert not errors and p["interests"][0]["id"] == "graphrag"
    assert p["interests"][1]["id"].startswith("interest-")             # 한글 topic은 해시 slug


def errs(data):
    return ip.parse_profile(data, TODAY)[1]


def test_profile_format_errors():
    ok = {"topic": "T", "description": "D"}
    assert any("version" in e for e in errs({"interests": [ok]}))
    assert any("version" in e for e in errs({"version": "1", "interests": [ok]}))
    assert any("interests" in e for e in errs({"version": 1}))
    assert any("topic" in e for e in errs({"version": 1, "interests": [{"description": "D"}]}))
    assert any("description" in e for e in errs({"version": 1, "interests": [{"topic": "T"}]}))
    assert any("priority" in e for e in errs({"version": 1, "interests": [dict(ok, priority="urgent")]}))
    assert any("모르는 필드" in e and "aliass" in e for e in errs({"version": 1, "interests": [dict(ok, aliass=["x"])]}))
    assert any("id 중복" in e for e in errs({"version": 1, "interests": [dict(ok, id="a"), dict(ok, id="a")]}))
    assert any("id 중복" in e for e in errs({"version": 1, "interests": [{"topic": "Same", "description": "D"},
                                                                       {"topic": "same", "description": "D"}]}))   # 자동 slug 충돌도 잡는다
    assert any("examples" in e and "목록" in e for e in errs({"version": 1, "interests": [dict(ok, examples="SPLADE")]}))
    assert any("relates_to" in e for e in errs({"version": 1, "interests": [dict(ok, relates_to=["adr-4"])]}))
    assert any("active" in e for e in errs({"version": 1, "interests": [dict(ok, active="yes")]}))
    assert any("review_after" in e and "added" in e for e in errs(
        {"version": 1, "interests": [dict(ok, added="2026-10-06", review_after="2026-10-01")]}))
    assert any("added" in e for e in errs({"version": 1, "interests": [dict(ok, added="어제")]}))
    assert any("excludes[0]" in e and "topic" in e for e in errs({"version": 1, "interests": [ok], "excludes": [{"why": "x"}]}))
    assert any("최상위" in e for e in errs({"version": 1, "interests": [ok], "extra": 1}))


def test_profile_reports_all_errors_at_once():
    data = {"version": 0, "interests": [{"topic": "", "description": "", "priority": "x"}, {"topic": "T"}]}
    assert len(errs(data)) >= 5


def test_profile_review_warning_and_file_load():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.yaml"
        f.write_text("version: 1\ninterests:\n  - {topic: T, description: D, added: 2026-01-01}\n", encoding="utf-8")
        p, warnings = ip.load_profile(f, TODAY)
        assert p["interests"][0]["review_after"] == "2026-01-29" and any("review_after" in w for w in warnings)
        f.write_text("version: 1\ninterests: [ {", encoding="utf-8")
        try:
            ip.load_profile(f, TODAY)
            raise AssertionError("깨진 YAML이 통과했다")
        except ip.ProfileError as e:
            assert "YAML" in e.errors[0]
        f.write_text("version: 1\ninterests: []\n", encoding="utf-8")
        try:
            ip.load_profile(f, TODAY)
            raise AssertionError("빈 관심사가 통과했다")
        except ip.ProfileError:
            pass


# ---------------- 레인 ----------------

def test_lane_split():
    res = profile_filter.run_filter(ALL, profile(), ["kw", "kw+aliases"])
    a_ids = {x["id"] for x in res["lane_a"]}
    assert a_ids == {x["id"] for x in A_ITEMS}
    for s, ranked in res["lane_b"].items():
        ids = {e["id"] for e in ranked}
        assert not ids & a_ids, f"{s}: 레인 A 항목이 레인 B에도 있다"
    assert res["counts"]["skipped_pages"] == 1 and res["counts"]["lane_b_candidates"] == len(B_ITEMS)
    reasons = {x["id"]: x["reasons"] for x in res["lane_a"]}
    assert reasons["github-release:pydantic/pydantic@v3.0.0"] == ["dependency-release:pydantic"]
    assert reasons["vendor:openai:1"] == ["vendor-feed:openai"]
    assert reasons["vendor-diff:anthropic:u@2026-W41"] == ["page-diff:anthropic"]       # page-diff는 vendor-feed로 세지 않는다
    assert all(x["gate"] is None and x["gate_status"] == "not-implemented" for x in res["lane_a"])


def test_lane_a_keeps_both_in_matched_by():
    res = profile_filter.run_filter(ALL, profile(), ["kw", "kw+aliases"])
    x = {e["id"]: e for e in res["lane_a"]}["vendor:openai:1"]
    mb = x["matched_by"]["kw+aliases"]
    assert mb[0]["kind"] == "lane" and mb[0]["lane"] == "A"
    assert any(e["kind"] == "match" and e["interest"] == "hybrid-sparse" and e["term"] == "hybrid search" for e in mb)
    assert x["also_matches_interest"] == {"kw": False, "kw+aliases": True}
    pyd = {e["id"]: e for e in res["lane_a"]}["github-release:pydantic/pydantic@v3.0.0"]
    assert pyd["also_matches_interest"]["kw+aliases"] is False and len(pyd["matched_by"]["kw"]) == 1   # 레인 표시만


def test_only_new():
    items = [it("hn:1", "SPLADE old", new=False), it("hn:2", "SPLADE fresh", new=True), it("hn:3", "SPLADE nokey")]
    del items[2]["new"]
    all_ids = {e["id"] for e in profile_filter.run_filter(items, profile(), ["kw"])["lane_b"]["kw"]}
    new_ids = {e["id"] for e in profile_filter.run_filter(items, profile(), ["kw"], only_new=True)["lane_b"]["kw"]}
    assert all_ids == {"hn:1", "hn:2", "hn:3"} and new_ids == {"hn:2", "hn:3"}       # 개발용 원자료(new 칸 없음)는 새 것으로 친다


def test_stack_terms_and_mentions():
    md = "### 의존성 → component\n\n| 의존성 | component |\n|---|---|\n| PyYAML | skill |\n| langgraph · langchain-core | product |\n| litellm (별도 venv) | x |\n"
    terms = lanes.stack_terms_from_claude_md(md)
    assert terms == ["PyYAML", "langgraph", "langchain-core", "litellm"], terms
    assert lanes.stack_mentions(it("hn:1", "Using LangGraph for agents"), terms) == ["langgraph"]
    assert lanes.stack_mentions(it("hn:1", "langgraphs everywhere"), terms) == []         # 단어 경계


# ---------------- 매칭·점수 ----------------

def test_word_boundary_and_korean():
    m = lambda title, term: bool(keyword_match.where_found(it("hn:1", title), term))  # noqa: E731
    assert m("TeleOCR model", "TeleOCR")
    assert not m("TeleOCR model", "OCR")                    # 단어 안의 부분 문자열은 걸리지 않는다
    assert m("an OCR model", "ocr")                         # 대소문자 무시
    assert m("RRF-based fusion", "RRF")                     # 하이픈은 경계
    assert not m("ERRF2", "RRF")
    assert m("멀티모달 모델", "멀티모달") and m("초멀티모달화", "멀티모달")      # 한국어는 부분 일치
    assert m("pass^k metric", "pass^k")                     # 정규식 특수문자는 글자 그대로
    assert keyword_match.where_found(it("hn:1", "x", text="OCR here", url="https://a/ocr"), "OCR") == ["text", "url"]


def test_topic_terms():
    assert keyword_match.topic_terms("그래프 기반 검색 (GraphRAG)") == ["그래프 기반 검색 (GraphRAG)", "그래프 기반 검색", "GraphRAG"]
    assert keyword_match.topic_terms("음성 인식·합성 (STT·TTS)") == ["음성 인식·합성 (STT·TTS)", "음성 인식·합성", "STT", "TTS"]
    assert keyword_match.topic_terms("강화학습 (DPO·ORPO 등)")[-2:] == ["DPO", "ORPO"]
    assert keyword_match.topic_terms("멀티모달") == ["멀티모달"]


def test_scores_by_hand():
    kw = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw"))
    kwa = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw+aliases"))
    assert kw["hn:1"]["score"] == 1.5                        # SPLADE (high 1.5) 한 단어
    assert kw["hn:2"]["score"] == 1.5 and kwa["hn:2"]["score"] == 1.875      # +aliases로 두 번째 단어 → (1+0.25)*1.5
    assert "hn:3" not in kw                                  # 별칭으로만 걸린다
    assert kw["hn:7"]["score"] == 1.0 and kwa["hn:7"]["score"] == 1.25      # normal: id multimodal / +OCR
    assert "hn:8" not in kw and kwa["hn:8"]["score"] == 0.5               # low 0.5
    assert "hn:9" not in kw and "hn:9" not in kwa and "hn:10" not in kwa     # active:false · 무관
    ranked = keyword_match.rank_items(B_ITEMS, profile(), "kw+aliases")
    assert [e["rank"] for e in ranked] == list(range(1, len(ranked) + 1))
    assert [e["score"] for e in ranked] == sorted([e["score"] for e in ranked], reverse=True)


def test_matched_by_records_field_and_word():
    kw = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw"))
    mb = kw["hn:2"]["matched_by"]
    assert {"kind": "match", "interest": "hybrid-sparse", "field": "topic", "term": "RRF", "where": ["title"]} in mb
    assert {"kind": "interest", "interest": "hybrid-sparse", "priority": "high", "points": 1.5} in mb
    kwa = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw+aliases"))
    assert {"kind": "match", "interest": "hybrid-sparse", "field": "aliases", "term": "hybrid search", "where": ["text"]} in kwa["hn:2"]["matched_by"]


def test_penalties():
    kw = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw"))
    kwa = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw+aliases"))
    # 관심사 not: hn:3은 hybrid가 걸렸고 web search API가 든다 → 1.5 - 1.0
    assert kwa["hn:3"]["score"] == 0.5
    pen = [m for m in kwa["hn:3"]["matched_by"] if m["kind"] == "penalty"]
    assert pen == [{"kind": "penalty", "source": "not", "interest": "hybrid-sparse", "term": "web search API", "where": ["title"], "points": -1.0}]
    # 한국어 not도 그대로 부분 일치: 멀티모달 1.0 - 1.0
    assert kw["hn:6"]["score"] == 0.0
    # 전역 excludes: hn:4는 TeleOCR(1.0) - image generation tool(1.0)
    assert kw["hn:4"]["score"] == 0.0
    pen = [m for m in kw["hn:4"]["matched_by"] if m["kind"] == "penalty"]
    assert len(pen) == 1 and pen[0]["source"] == "exclude" and pen[0]["exclude"] == "image generation tool" and pen[0]["points"] == -1.0
    # 감점만 있는 항목은 순위에 오르지 않는다 (제외는 감점이지 후보를 만들지 않는다)
    assert "hn:5" not in kw and "hn:5" not in kwa
    # 관심사 not은 그 관심사에만: speech 관심사엔 not이 없으니 hybrid의 not 단어가 있어도 speech 점수는 안 깎인다
    x = it("hn:20", "TTS demo with a web search API")
    s = keyword_match.score_item(x, profile(), "kw+aliases")
    assert s["score"] == 0.5 and not [m for m in s["matched_by"] if m["kind"] == "penalty"]


def test_strategies_differ_only_by_aliases():
    p = profile()
    only_alias = it("hn:30", "Rank fusion in practice")
    assert keyword_match.score_item(only_alias, p, "kw") is None
    assert keyword_match.score_item(only_alias, p, "kw+aliases")["score"] == 1.5
    assert keyword_match.STRATEGIES["kw"] == ("id", "topic", "examples")
    for item in B_ITEMS:                                      # aliases가 kw 점수를 바꾸면 안 된다
        s = keyword_match.score_item(item, p, "kw")
        assert s is None or all(m.get("field") != "aliases" for m in s["matched_by"]), item["id"]


def test_known_example():
    kw = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw"))
    kwa = by_id(keyword_match.rank_items(B_ITEMS, profile(), "kw+aliases"))
    assert kw["hn:1"]["known_example"] and kw["hn:4"]["known_example"] and kwa["hn:1"]["known_example"]
    assert not kw["hn:2"]["known_example"] and not kwa["hn:2"]["known_example"]    # RRF는 topic이지 examples가 아니다
    assert not kwa["hn:7"]["known_example"] and not kwa["hn:8"]["known_example"]   # 별칭(OCR·TTS)은 알려진 정답이 아니다
    assert keyword_match.known_examples(it("hn:9", "ZebraNet"), profile()) == []     # 꺼 둔 관심사의 예시는 세지 않는다
    assert keyword_match.known_examples(it("hn:1", "BGE-M3 and SPLADE"), profile()) == [
        {"interest": "hybrid-sparse", "term": "SPLADE"}, {"interest": "hybrid-sparse", "term": "BGE-M3"}]


def test_unknown_strategy():
    try:
        keyword_match.rank_items(B_ITEMS, profile(), "vector")
        raise AssertionError("없는 전략이 통과했다")
    except KeyError:
        pass


# ---------------- pooling ----------------

def rankings():
    r = profile_filter.run_filter(ALL, profile(), ["kw", "kw+aliases"])["lane_b"]
    return r


def test_pool_dedupe_and_hidden_strategy():
    r = rankings()
    pool, key = judging.build_pool(r, 3)
    ids = [e["id"] for e in pool]
    assert len(ids) == len(set(ids))                                      # 중복 제거
    want = {e["id"] for s in r.values() for e in s[:3]}
    assert set(ids) == want and len(want) < 6                             # 두 전략 상위 3개가 겹친다
    assert key["pool_depth"] == 3 and set(key["items"]) == want
    k1 = key["items"]["hn:1"]
    assert set(k1["ranks"]) == {"kw", "kw+aliases"} and k1["known_example"] is True
    # 전략 순서를 따르지 않는다: 풀 깊이를 끝까지 잡으면 두 순위와 모두 다른 순서여야 한다
    pool, key = judging.build_pool(r, 99)
    ids = [e["id"] for e in pool]
    assert len(ids) > 4
    for s in r:
        order = [e["id"] for e in r[s] if e["id"] in set(ids)]
        assert ids != order, f"시트 순서가 {s}의 순위 순서와 같다 — 전략이 드러난다"
    assert ids != sorted(ids)
    assert judging.build_pool(r, 99)[0] == pool                          # 같은 seed면 같은 순서 (재현)


def test_sheet_hides_strategy_and_rank():
    r = rankings()
    pool, key = judging.build_pool(r, 99)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "sheet.csv"
        judging.write_sheet(f, pool, {"hn:2": "We compare hybrid search variants"})
        raw = f.read_text(encoding="utf-8-sig")
        rows = list(csv.DictReader(raw.splitlines()))
    assert list(rows[0]) == judging.COLUMNS and all(r_["relevant"] == "" for r_ in rows)
    for banned in ("kw+aliases", "rank", "score", "strategy", "matched_by", "known_example", "penalty"):
        assert banned not in raw.lower().split("item_id", 1)[1].replace("snippet", "").replace("relevant", ""), banned
    assert not any(c in judging.COLUMNS for c in ("rank", "score", "strategy"))
    assert {x["item_id"]: x["snippet"] for x in rows}["hn:2"] == "We compare hybrid search variants"


def test_read_judgments():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "s.csv"
        f.write_text("item_id,relevant,title\na,1,x\nb,N,x\nc,,x\nd,y,x\n", encoding="utf-8-sig")
        rel, blank = judging.read_judgments(f)
        assert rel == {"a": 1, "b": 0, "d": 1} and blank == ["c"]
        f.write_text("item_id,relevant\na,maybe\n", encoding="utf-8")
        try:
            judging.read_judgments(f)
            raise AssertionError("알 수 없는 값이 통과했다")
        except ValueError as e:
            assert "maybe" in str(e)
        f.write_text("item_id,relevant\nz,1\n", encoding="utf-8")
        try:
            judging.read_judgments(f, pool_ids=["a"])
            raise AssertionError("풀 밖 판정이 통과했다")
        except ValueError:
            pass


# ---------------- 지표 (손 계산) ----------------

def ranking(ids, known=()):
    return [{"id": i, "rank": n, "known_example": i in known} for n, i in enumerate(ids, 1)]


def test_metrics_by_hand():
    rk = {"S": ranking(["a", "b", "c", "d", "e"])}
    rel = {"a": 1, "b": 0, "c": 1, "d": 0, "e": 1, "f": 1}              # f는 어느 순위에도 없는 정답 → R = 4
    res = judging.evaluate(rk, rel, [3, 5])
    m3, m5 = res["strategies"]["S"][3], res["strategies"]["S"][5]
    assert res["relevant_total"] == 4
    assert m3["hits"] == 2 and m3["recall"] == 2 / 4 and abs(m3["noise"] - 1 / 3) < 1e-12
    # DCG@3 = 1/log2(2) + 1/log2(4) = 1.5 ; IDCG@3 = 1 + 1/log2(3) + 1/log2(4) = 1 + 0.6309298 + 0.5
    assert abs(m3["ndcg"] - 1.5 / 2.1309297536) < 1e-6
    # DCG@5 = 1 + 0.5 + 1/log2(6)=0.3868528 ; IDCG@5(R=4) = 1 + 0.6309298 + 0.5 + 1/log2(5)=0.4306766
    assert m5["hits"] == 3 and m5["recall"] == 3 / 4 and abs(m5["noise"] - 0.4) < 1e-12
    assert abs(m5["ndcg"] - 1.8868528 / 2.5616064) < 1e-6
    assert m3["unjudged"] == 0


def test_metrics_unjudged_and_empty():
    rk = {"S": ranking(["a", "b", "x"])}                                   # x는 풀 밖 — 관련 없음으로 치되 건수를 센다
    res = judging.evaluate(rk, {"a": 1, "b": 0}, [3])
    m = res["strategies"]["S"][3]
    assert m["unjudged"] == 1 and m["recall"] == 1.0 and abs(m["noise"] - 2 / 3) < 1e-12
    none = judging.evaluate(rk, {"a": 0, "b": 0}, [3])                     # 정답이 하나도 없으면 recall·nDCG는 정의되지 않는다
    assert none["strategies"]["S"][3]["recall"] is None and none["strategies"]["S"][3]["ndcg"] is None
    assert judging.evaluate({"S": []}, {"a": 1}, [3])["strategies"]["S"][3]["noise"] is None


def test_metrics_exclude_known_examples():
    rk = {"S": ranking(["a", "b", "c"], known={"a"})}
    rel = {"a": 1, "b": 1, "c": 0}
    full = judging.evaluate(rk, rel, [1])["strategies"]["S"][1]
    ex = judging.evaluate(rk, rel, [1], exclude_known=True)
    assert full["recall"] == 0.5 and full["hits"] == 1
    assert ex["relevant_total"] == 1 and ex["strategies"]["S"][1]["recall"] == 1.0     # a를 순위·정답에서 모두 뺀다 → 1위는 b


def test_score_run_refuses_without_judgments():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        profile_path = d / "p.yaml"
        profile_path.write_text(PROFILE_YAML, encoding="utf-8")
        snap = d / "snap"
        snap.mkdir()
        (snap / "items.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in ALL), encoding="utf-8")
        out = d / "out"
        assert profile_filter.main(["run", "--snapshot", str(snap), "--profile", str(profile_path), "--out", str(out), "--no-stack"]) == 0
        assert profile_filter.main(["pool", "--run", str(out), "--pool-depth", "4"]) == 0
        assert profile_filter.main(["score", "--run", str(out), "--cutoffs", "2,4"]) == 2      # 시트가 비어 있다 → 계산하지 않음
        assert not (out / "scores.json").exists()
        sheet = out / judging.SHEET
        rows = list(csv.DictReader(sheet.read_text(encoding="utf-8-sig").splitlines()))
        for n, row in enumerate(rows):                                                      # 일부만 채운다
            row["relevant"] = "1" if n % 2 == 0 else ""
        with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=judging.COLUMNS)
            w.writeheader()
            w.writerows(rows)
        assert profile_filter.main(["score", "--run", str(out), "--cutoffs", "2,4"]) == 2      # 일부만 → 거부
        assert profile_filter.main(["score", "--run", str(out), "--cutoffs", "2,4", "--partial"]) == 0
        scores = json.loads((out / "scores.json").read_text(encoding="utf-8"))
        assert scores["blank"] > 0 and set(scores["all"]["strategies"]) == {"kw", "kw+aliases"}


def test_cli_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "p.yaml").write_text(PROFILE_YAML, encoding="utf-8")
        snap = d / "snap"
        snap.mkdir()
        (snap / "items.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in ALL), encoding="utf-8")
        out = d / "out"
        assert profile_filter.main(["validate", "--profile", str(d / "p.yaml")]) == 0
        assert profile_filter.main(["run", "--snapshot", str(snap / "items.jsonl"), "--profile", str(d / "p.yaml"),
                                    "--strategy", "kw,kw+aliases", "--out", str(out), "--no-stack"]) == 0
        manifest = json.loads((out / "run.json").read_text(encoding="utf-8"))
        assert manifest["profile_version"] == 3 and manifest["counts"]["lane_a"] == 3
        assert (out / "lane_a.json").exists() and (out / "lane_b.kw.json").exists() and (out / "lane_b.kw+aliases.json").exists()
        assert profile_filter.main(["pool", "--run", str(out), "--top", "99"]) == 0
        sheet = out / judging.SHEET
        rows = list(csv.DictReader(sheet.read_text(encoding="utf-8-sig").splitlines()))
        good = {"hn:1", "hn:2", "hn:7"}
        for row in rows:
            row["relevant"] = "1" if row["item_id"] in good else "0"
        with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=judging.COLUMNS)
            w.writeheader()
            w.writerows(rows)
        assert profile_filter.main(["score", "--run", str(out), "--cutoffs", "3"]) == 0
        sc = json.loads((out / "scores.json").read_text(encoding="utf-8"))
        kw3, kwa3 = sc["all"]["strategies"]["kw"]["3"], sc["all"]["strategies"]["kw+aliases"]["3"]
        # kw 순위: hn:1(1.5) hn:2(1.5) hn:7(1.0) hn:4(0.0)... → 3개 모두 정답 · kw+aliases: hn:2(1.875) hn:1(1.5) hn:7(1.25)
        assert kw3["hits"] == 3 and kw3["recall"] == 1.0 and kw3["noise"] == 0.0 and abs(kw3["ndcg"] - 1.0) < 1e-9
        assert kwa3["hits"] == 3 and kwa3["recall"] == 1.0
        assert sc["known_examples_in_pool"]["count"] >= 2


# ---------------- 변이 검사 (규율 3) ----------------

class Mutation:
    """with Mutation(모듈, 이름, 값): — 끝나면 원래대로. 캐시된 정규식도 비운다."""

    def __init__(self, mod, name, value):
        self.mod, self.name, self.value = mod, name, value

    def __enter__(self):
        self.old = getattr(self.mod, self.name)
        setattr(self.mod, self.name, self.value)
        keyword_match._cache.clear()

    def __exit__(self, *exc):
        setattr(self.mod, self.name, self.old)
        keyword_match._cache.clear()


_ORIG_SPLIT = lanes.split_lanes


def _lane_overlap(items):
    a, b, skipped = _ORIG_SPLIT(items)
    return a, b + [x for x, _ in a], skipped


def _substring_regex(term):
    import re
    return re.compile(re.escape(term.strip()), re.I)


def _known_with_aliases(item, prof):
    out = []
    for i in prof["interests"]:
        if i["active"]:
            out += [{"interest": i["id"], "term": t} for t in i["examples"] + i["aliases"] if keyword_match.where_found(item, t)]
    return out


def mutations():
    kw_aliases = keyword_match.STRATEGIES["kw+aliases"]
    return [
        ("감점 무시 (not·exclude 0점)", [(keyword_match, "NOT_PENALTY", 0.0), (keyword_match, "EXCLUDE_PENALTY", 0.0)], test_penalties),
        ("aliases를 kw에도 사용", [(keyword_match, "STRATEGIES", {"kw": kw_aliases, "kw+aliases": kw_aliases})], test_strategies_differ_only_by_aliases),
        ("aliases를 kw에도 사용 (점수 표)", [(keyword_match, "STRATEGIES", {"kw": kw_aliases, "kw+aliases": kw_aliases})], test_scores_by_hand),
        ("레인 A·B 중복 허용", [(lanes, "split_lanes", _lane_overlap)], test_lane_split),
        ("단어 경계 없이 부분 일치", [(keyword_match, "term_regex", _substring_regex)], test_word_boundary_and_korean),
        ("시트를 id 순으로 (섞지 않음)", [(judging, "shuffle_key", lambda seed, i: i)], test_pool_dedupe_and_hidden_strategy),
        ("nDCG 할인 오류", [(judging, "_discount", lambda p: p)], test_metrics_by_hand),
        ("알려진 정답에 aliases 포함", [(keyword_match, "known_examples", _known_with_aliases)], test_known_example),
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

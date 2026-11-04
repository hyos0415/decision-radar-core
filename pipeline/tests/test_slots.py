"""자리 질의(pipeline/slots.py, HYO-168 나) 오프라인 테스트 — 모델 없이 (stub · 가짜 claude CLI).

  python pipeline/tests/test_slots.py

Terra 모양을 재현한다: OpenAI changelog 블록 「Released GPT-6.1 Terra」 · HN 637점 · Bedrock What's New가 이름 기반 관문에서 빠졌다.
- 후보 모으기는 결정론 (출처별 규칙) · 판정은 모델 → 스크립트가 id · 자리 · 점수를 검사 · 순위는 적합도 → 공식 먼저 → 인기
- detect 입력 · 목록(list.json) 모두 자리마다 top-3 (kind model_release · HN은 community) — 변경 2026-11-04: 처음엔 detect는 1위만
- 구독으로만: 가짜 CLI가 API 키를 보면 실패한다
뒤쪽 변이 검사(규율 3): 문턱을 없애거나 · 모델 답을 검사 없이 믿거나 · API 키를 안 지우거나 · 자리마다 1위만 detect로 보내면(Terra W44 2위가 빠진다) 위 테스트가 빨개져야 한다.
"""
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import detect_feed, slots  # noqa: E402

SLOTS = {"version": 1, "slots": [
    {"id": "gate-classifier", "name": "관문 분류", "repo": "decision-radar", "role": "분류", "current": ["OpenAI gpt-6.1-sol (API)"], "constraints": ["구조화 출력"]},
    {"id": "fineprint-extraction", "name": "조건 추출", "repo": "fineprint", "role": "추출", "current": ["Anthropic claude-haiku-4-5"], "constraints": ["API"]},
    {"id": "medical-ondevice", "name": "온디바이스", "repo": "medical-mate-ai", "role": "폰", "current": ["Qwen3.5-2B Q4_0"], "constraints": ["2~3B"]},
]}

CL = "https://platform.openai.com/docs/changelog"


def _it(i, source, title, url, text="", channels=(), signals=None, meta=None):
    return {"id": i, "source": source, "title": title, "url": url, "text": text, "channels": list(channels),
            "signals": signals or {}, "meta": meta or {}, "created_at": "2026-10-27T07:00:00Z"}


ITEMS = [
    _it(f"vendor-diff:openai:{CL}@2026-W44#oct-26", "vendor", "[openai] changelog Oct 26: Feature · gpt-6.1-terra", f"{CL}#oct-26",
        "Feature\ngpt-6.1-terra\nReleased GPT-6.1 Terra , a fast, efficient model", ["vendor:openai"],
        meta={"vendor": "openai", "kind": "page-diff", "parent": f"vendor-diff:openai:{CL}@2026-W44", "entry_date": "Oct 26"}),
    _it(f"vendor-diff:openai:{CL}@2026-W44#oct-21", "vendor", "[openai] changelog Oct 21: service tiers", f"{CL}#oct-21",
        "Feature\nYou can now choose which service tiers projects can use", ["vendor:openai"],
        meta={"vendor": "openai", "kind": "page-diff", "parent": f"vendor-diff:openai:{CL}@2026-W44"}),        # 출시 문구 없음
    _it("vendor:aws-bedrock:t", "vendor", "OpenAI GPT-6.1 Terra is now generally available on Amazon Bedrock",
        "https://aws.amazon.com/about-aws/whats-new/2026/10/openai-gpt-6-1-terra-on-amazon-bedrock/", channels=["vendor:aws-bedrock"]),
    _it("github-release:anthropics/claude-code@v2.1.311", "vendor", "anthropics/claude-code v2.1.311",
        "https://github.com/anthropics/claude-code/releases/tag/v2.1.311", "Released fixes", ["vendor:anthropic"]),   # 벤더 릴리스 — 자리 후보 아님
    _it("hn:1", "hn", "GPT-6.1 Terra", "https://openai.com/index/introducing-gpt-6-1-terra/", signals={"points": 637}),
    _it("hn:2", "hn", "GPT 6.1 Terra", "https://developers.openai.com/api/docs/models/gpt-6.1-terra", signals={"points": 37}),   # 문턱 아래
    _it("hn:3", "hn", "Patrick Stewart has died", "https://example.com/ps", signals={"points": 900}),                            # 모델 이름 꼴 없음
    _it("reddit:nm", "reddit", "baidu/Unlimited-OCR-2 · Hugging Face", "https://www.reddit.com/r/LocalLLaMA/comments/x",
        channels=["r/LocalLLaMA:flair:New Model"], signals={"rank:r/LocalLLaMA:flair:New Model": 17}),
    _it("reddit:q", "reddit", "Future of RAG", "https://www.reddit.com/r/LangChain/comments/y", channels=["r/LangChain:top-week"],
        signals={"rank:r/LangChain:top-week": 1}),
    _it("hf-model:org/Small-2B", "huggingface", "org/Small-2B", "https://huggingface.co/org/Small-2B", channels=["trending"],
        signals={"rank:trending": 3}),
    _it("hf-model:org/Far-9B", "huggingface", "org/Far-9B", "https://huggingface.co/org/Far-9B", channels=["trending"],
        signals={"rank:trending": 80}),                                                                                       # 순위 밖
]

TERRA_CL = f"vendor-diff:openai:{CL}@2026-W44#oct-26"


def judged_stub_terra():
    """모델 답 흉내: Terra 세 소식 → 관문 자리. 하나는 문턱 아래(1). 잘못된 id · 자리 · 점수도 섞는다."""
    return {"judgments": [
        {"id": TERRA_CL, "release": True, "matches": [{"slot": "gate-classifier", "fit": 2, "reason": "같은 GPT 6.1 계열의 싼 모델"}]},
        {"id": "hn:1", "release": True, "matches": [{"slot": "gate-classifier", "fit": 3, "reason": "sol보다 싸고 빠르다"}]},
        {"id": "vendor:aws-bedrock:t", "release": True, "matches": [{"slot": "gate-classifier", "fit": 2, "reason": "Bedrock 제공"},
                                                                     {"slot": "medical-ondevice", "fit": 1, "reason": "크기 모름"}]},
        {"id": "reddit:nm", "release": True, "matches": [{"slot": "nope", "fit": 3, "reason": "없는 자리"}]},
        {"id": "hf-model:org/Small-2B", "release": True, "matches": [{"slot": "medical-ondevice", "fit": 7, "reason": "범위 밖 점수"}]},
        {"id": "made-up", "release": True, "matches": [{"slot": "gate-classifier", "fit": 3, "reason": "지어낸 id"}]},
        {"id": "reddit:nm2", "release": False, "matches": []},
    ]}


def test_collect_rules():
    got = {c["id"]: c["why"] for c in slots.collect(ITEMS)}
    assert set(got) == {TERRA_CL, "vendor:aws-bedrock:t", "hn:1", "reddit:nm", "hf-model:org/Small-2B"}, got
    assert got[TERRA_CL] == "changelog:openai" and got["hn:1"] == "hn:637" and got["hf-model:org/Small-2B"] == "hf-trending:3"


def test_validate_drops_bad_answers():
    cands = slots.collect(ITEMS)
    got, dropped = slots.validate(judged_stub_terra(), cands, SLOTS)
    assert dropped == {"unknown_id": 2, "unknown_slot": 1, "bad_fit": 1}, dropped
    assert got["reddit:nm"]["matches"] == [] and got["hf-model:org/Small-2B"]["matches"] == []


def test_rank_threshold_order_topk():
    cands = slots.collect(ITEMS)
    got, _ = slots.validate(judged_stub_terra(), cands, SLOTS)
    per = slots.rank(cands, got, SLOTS)
    assert [(x["id"], x["rank"]) for x in per["gate-classifier"]] == [("hn:1", 1), (TERRA_CL, 2), ("vendor:aws-bedrock:t", 3)], per["gate-classifier"]
    assert per["medical-ondevice"] == [] and per["fineprint-extraction"] == []        # 문턱 아래(1)는 「없음」
    assert len(slots.rank(cands, got, SLOTS, k=2)["gate-classifier"]) == 2


def test_run_stub_and_to_detect():
    res = slots.run(ITEMS, SLOTS, engine="stub")
    assert res["candidates"] == 5 and res["unjudged"] == [] and res["to_detect"] == [x["id"] for x in res["per_slot"]["gate-classifier"]], res["to_detect"]


def _fake_cli(d, answer):
    p, a = Path(d) / "fake_claude.py", Path(d) / "answer.json"
    a.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    p.write_text("import json, os, sys\n"
                 "sys.stdin.read()\n"
                 "if os.environ.get('ANTHROPIC_API_KEY'):\n"
                 "    print(json.dumps({'is_error': True, 'subtype': 'api-key-seen', 'result': 'API key billing'})); sys.exit(0)\n"
                 f"so = json.load(open({str(a)!r}, encoding='utf-8'))\n"
                 "print(json.dumps({'is_error': False, 'subtype': 'success', 'structured_output': so, 'total_cost_usd': 0.1}))\n",
                 encoding="utf-8")
    return str(p)


def test_claude_engine_subscription_only_and_raw_saved():
    with tempfile.TemporaryDirectory() as d:
        old = {k: os.environ.get(k) for k in ("RADAR_CLAUDE_BIN", "ANTHROPIC_API_KEY")}
        os.environ["RADAR_CLAUDE_BIN"] = _fake_cli(d, judged_stub_terra())
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        try:
            res = slots.run(ITEMS, SLOTS, engine="claude", out=d)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        assert res["per_slot"]["gate-classifier"][0]["id"] == "hn:1"
        raw = json.loads((Path(d) / "slots-raw.json").read_text(encoding="utf-8"))
        assert raw["billing"] == "subscription" and raw["structured_output"]["judgments"]
        again = slots.run(ITEMS, SLOTS, engine="stub", out=d, reuse=True)   # reuse면 저장한 답을 쓴다 (엔진을 안 부른다)
        assert again["per_slot"]["gate-classifier"][0]["id"] == "hn:1"


def test_detect_feed_adds_slot_picks_and_list():
    with tempfile.TemporaryDirectory() as d:
        snap, run, out = Path(d) / "snap", Path(d) / "run", Path(d) / "out"
        snap.mkdir(), run.mkdir()
        (snap / "items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in ITEMS), encoding="utf-8")
        gate = {"keep": True, "status": "ok", "reason": "L1 × security", "kept_by": [{"tier": "L1", "change": "security", "technology": "electron"}]}
        lane_a = [{"id": "vendor:aws-bedrock:t", "title": "bedrock", "url": ITEMS[2]["url"], "lane": "A", "gate": gate, "gate_status": "ok",
                   "shown": True, "listed": True, "always": True, "query": "Amazon Bedrock", "rank": None}]
        (run / "lane_a.json").write_text(json.dumps(lane_a), encoding="utf-8")
        (run / "run.json").write_text(json.dumps({"snapshot": str(snap), "counts": {"items": len(ITEMS), "lane_a": 1},
                                                  "lane_a_gate": {"model": "stub", "mode": "topk", "total_kept": 1, "label": "1/1 · 목록 1",
                                                                  "listed": [{"id": "vendor:aws-bedrock:t"}]}}), encoding="utf-8")
        cands = slots.collect(ITEMS)
        got, _ = slots.validate(judged_stub_terra(), cands, SLOTS)
        res = slots.run(ITEMS, SLOTS, engine="stub")
        res["per_slot"] = slots.rank(cands, got, SLOTS)
        (run / "slots.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        f = detect_feed.build(run, out)
        feed = json.loads((out / "feed.json").read_text(encoding="utf-8"))
        kinds = {e["id"]: (e["kind"], e["source_type"]) for e in feed}
        assert kinds == {"vendor:aws-bedrock:t": ("vendor_news", "official_doc"), "hn:1": ("model_release", "community"),
                         TERRA_CL: ("model_release", "official_doc")}, kinds        # 자리 top-3 전부 — Terra changelog 블록(2위)도 · Bedrock은 관문 몫으로 한 번
        assert f["to_detect"] == 3 and f["slots"]["listed"] == 3 and "자리 질의" in detect_feed.funnel_line(f)
        lst = json.loads((out / "list.json").read_text(encoding="utf-8"))
        assert [x["id"] for x in lst["always"]] == ["vendor:aws-bedrock:t"]
        g = next(s for s in lst["slots"] if s["slot"] == "gate-classifier")
        assert [(x["id"], x["to_detect"]) for x in g["items"]] == [("hn:1", True), (TERRA_CL, True), ("vendor:aws-bedrock:t", True)], g["items"]
        why = json.loads((out / "gate.json").read_text(encoding="utf-8"))
        assert why["https://openai.com/index/introducing-gpt-6-1-terra/"]["slot"] == "gate-classifier"


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_validate, orig_picks = slots.validate, detect_feed.slot_picks

    def trust(so, cands, sl):     # 모델 답을 검사 없이
        return {j["id"]: {"release": j["release"], "matches": j["matches"]} for j in so["judgments"]}, {"unknown_id": 0, "unknown_slot": 0, "bad_fit": 0}

    def firsts_only(sl):          # 자리마다 1위만 detect로 (11/4 오전 규칙)
        return {xs[0]["id"]: (s, xs[0]) for s, xs in ((sl or {}).get("per_slot") or {}).items() if xs}

    return [
        ("문턱 없음 (fit 1도 목록에)", lambda: setattr(slots, "FIT_MIN", 0), lambda: setattr(slots, "FIT_MIN", 2)),
        ("모델 답을 검사 없이 믿음", lambda: setattr(slots, "validate", trust), lambda: setattr(slots, "validate", orig_validate)),
        ("API 키를 안 지움", lambda: setattr(slots, "STRIP_ENV", ()), lambda: setattr(slots, "STRIP_ENV", ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))),
        ("자리마다 1위만 detect로", lambda: setattr(detect_feed, "slot_picks", firsts_only), lambda: setattr(detect_feed, "slot_picks", orig_picks)),
        ("HN 점수 문턱 없음", lambda: setattr(slots, "HN_MIN_POINTS", 0), lambda: setattr(slots, "HN_MIN_POINTS", 100)),
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

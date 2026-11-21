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


SLOTS_V2 = {"version": 2, "slots": [
    {"id": "gate-classifier", "name": "관문 분류", "repo": "decision-radar", "role": "분류", "current": ["OpenAI gpt-6.1-sol"],
     "must": ["구조화된 답"], "now_via": ["OpenAI API 종량제"], "kinds": ["LLM", "판정 모델(decision model)"]},
    {"id": "retrieval-dense", "name": "검색 임베딩", "repo": "decision-radar", "role": "의미 검색", "current": ["BAAI/bge-m3"],
     "must": ["한국어 · 영어"], "now_via": ["로컬"], "kinds": ["임베딩 모델"]},
]}


def test_v2_roles_must_vs_now_via_and_switch_note():
    """자리 프로필 v2 (2026-11-12): 역할 조건(must)만 거르고 지금 쓰는 방식(now_via)은 「바꾸려면 필요한 것」(switch)으로 — v1은 그대로."""
    assert slots.system_for(SLOTS) is slots.SYSTEM and slots.system_for(SLOTS_V2) is slots.SYSTEM_V2
    cands = slots.collect(ITEMS)
    p1, p2 = slots.prompt(cands, SLOTS), slots.prompt(cands, SLOTS_V2)
    assert "constraints:" in p1 and "must:" not in p1
    assert "must:" in p2 and "now_via:" in p2 and "kinds:" in p2 and "constraints:" not in p2
    assert "now_via" in slots.SYSTEM_V2 and "깎지 않는다" in slots.SYSTEM_V2 and "switch" in slots.SYSTEM_V2
    so = {"judgments": [{"id": "hn:1", "release": True, "matches": [
        {"slot": "gate-classifier", "fit": 2, "reason": "더 싸다", "switch": "OpenAI API 그대로"}]},
        {"id": "hf-model:org/Small-2B", "release": True, "matches": [{"slot": "retrieval-dense", "fit": 2, "reason": "작은 임베딩"}]}]}
    got, dropped = slots.validate(so, cands, SLOTS_V2)
    per = slots.rank(cands, got, SLOTS_V2)
    assert per["gate-classifier"][0]["switch"] == "OpenAI API 그대로" and per["retrieval-dense"][0]["switch"] == ""   # 안 쓰면 빈 칸
    assert dropped == {"unknown_id": 0, "unknown_slot": 0, "bad_fit": 0}
    # 상한 없음 + 같은 모델 묶기: Terra 세 소식(같은 model) → 한 줄, 다른 모델은 상위 3 밖이어도 보인다
    many = {"judgments": [{"id": i, "release": True, "model": "GPT-6.1 Terra" if i in (TERRA_CL, "hn:1", "vendor:aws-bedrock:t") else f"m-{i}",
                           "matches": [{"slot": "gate-classifier", "fit": 2, "reason": "r"}]} for i in [c["id"] for c in cands]]}
    got, _ = slots.validate(many, cands, SLOTS_V2)
    gc = slots.rank(cands, got, SLOTS_V2)["gate-classifier"]
    assert len(gc) == len(cands) - 2, gc                                               # 5 후보 → Terra 3개가 1줄로, 나머지 2 = 3줄 (v1이면 상위 3에서 잘렸다)
    terra = [x for x in gc if x["model"] == "GPT-6.1 Terra"]
    assert len(terra) == 1 and terra[0]["id"] == TERRA_CL and sorted(terra[0]["also"]) == sorted(["hn:1", "vendor:aws-bedrock:t"]), terra
    # 넓은 후보: HF 작업별 순위 상위 · LocalLLaMA 출시 낱말 없는 상위 글 · News 꼬리표
    extra = [_it("hf-model:org/Decide-1B", "huggingface", "org/Decide-1B", "https://huggingface.co/org/Decide-1B",
                 signals={"rank:trending": 70, "rank:task:token-classification": 1}),
             _it("reddit:ll", "reddit", "New in llama.cpp: Decision Models", "https://www.reddit.com/r/LocalLLaMA/comments/z",
                 channels=["r/LocalLLaMA:top-week"], signals={"rank:r/LocalLLaMA:top-week": 17}),
             _it("reddit:news", "reddit", "llama, server: add /v1/systemone API", "https://www.reddit.com/r/LocalLLaMA/comments/w",
                 channels=["r/LocalLLaMA:flair:News"], signals={"rank:r/LocalLLaMA:flair:News": 14})]
    # 「확인 필요」: 최고 1점인 출시는 detect가 아니라 목록 한 줄 — 이미 2점 이상으로 보인 model은 빼고, 같은 model은 묶는다
    mixed = {"judgments": [
        {"id": "hn:1", "release": True, "model": "GPT-6.1 Terra", "matches": [{"slot": "gate-classifier", "fit": 2, "reason": "r"}]},
        {"id": "vendor:aws-bedrock:t", "release": True, "model": "GPT-6.1 Terra", "matches": [{"slot": "retrieval-dense", "fit": 1, "reason": "약함"}]},
        {"id": "reddit:nm", "release": True, "model": "Unlimited-OCR-2", "matches": [{"slot": "gate-classifier", "fit": 1, "reason": "정보 부족"}]},
        {"id": "hf-model:org/Small-2B", "release": True, "model": "unlimited-ocr-2", "matches": [{"slot": "retrieval-dense", "fit": 1, "reason": "r"}]},
        {"id": TERRA_CL, "release": False, "matches": []}]}
    got, _ = slots.validate(mixed, cands, SLOTS_V2)
    per = slots.rank(cands, got, SLOTS_V2)
    chk = slots.check_list(cands, got, per)
    assert [(x["id"], x.get("also")) for x in chk] == [("hf-model:org/Small-2B", ["reddit:nm"])], chk   # Terra는 2점으로 이미 보임 · 대표는 인기 순
    assert all(x["id"] not in {"reddit:nm", "hf-model:org/Small-2B"} for xs in per.values() for x in xs)          # detect(per_slot)엔 없다
    wide = {c["id"]: c["why"] for c in slots.collect(extra)}
    assert wide == {"hf-model:org/Decide-1B": "hf-task:token-classification:1", "reddit:ll": "reddit:top-week:17", "reddit:news": "reddit:news"}, wide


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

# --- HYO-244: 언어는 HF 태그로 코드가 확인 ---
LFM_TAGS = ["transformers", "safetensors", "en", "ar", "zh", "fr", "de", "it", "ja", "ko", "base_model:LiquidAI/LFM3-1.7B-Base",
            "base_model:finetune:LiquidAI/LFM3-1.7B-Base", "license:other"]
SLOTS_LANG = {"version": 2, "slots": [
    {"id": "medical-ondevice", "name": "온디바이스", "repo": "medical-mate-ai", "role": "폰", "current": ["Qwen3.5-2B"], "must": ["한국어"],
     "now_via": ["로컬"], "kinds": ["LLM"], "lang": "ko"},
    {"id": "gate-classifier", "name": "관문 분류", "repo": "decision-radar", "role": "분류", "current": ["x"], "must": ["구조화"],
     "now_via": ["API"], "kinds": ["LLM"]},
]}


def _hf(name, tags):
    return _it(f"hf-model:{name}", "huggingface", name, f"https://huggingface.co/{name}", channels=["trending"],
               signals={"rank:trending": 2}, meta={"tags": tags})


HF_ITEMS = [_hf("LiquidAI/LFM3-1.7B", LFM_TAGS), _hf("KRAFTON/Raon-1-nano", ["transformers", "safetensors"]),
            _hf("org/EnOnly-0.6B", ["en", "license:mit"]), _hf("org/Euro-27B", ["en", "fr", "de"]), _hf("org/Multi-E2B", ["multilingual", "en"])]


def _lang_so(missing_by_id, fit=1):
    return {"judgments": [{"id": f"hf-model:{n}", "release": True, "model": n, "matches": [
        {"slot": "medical-ondevice", "fit": fit, "reason": "언어 정보가 없어 한국어 확인 필요", **({"missing": mi} if mi is not None else {})},
        {"slot": "gate-classifier", "fit": 1, "reason": "다른 자리", "missing": ["language"]}]}
        for n, mi in missing_by_id.items()]}


def _run_lang(missing_by_id):
    cands = slots.collect(HF_ITEMS)
    judged, _ = slots.validate(_lang_so(missing_by_id), cands, SLOTS_LANG)
    return judged, slots.apply_lang(cands, judged, SLOTS_LANG)


def test_derive_fields_and_lang_status():
    c = {x["id"]: x for x in slots.collect(HF_ITEMS)}
    lfm = c["hf-model:LiquidAI/LFM3-1.7B"]
    assert "ko" in lfm["langs"] and lfm["base_model"] == "LiquidAI/LFM3-1.7B-Base" and lfm["size"] == "1.7B", lfm   # base_model:finetune:은 건너뛴다
    assert slots.lang_status(lfm)[0] == "pass"
    assert c["hf-model:KRAFTON/Raon-1-nano"]["langs"] == [] and slots.lang_status(c["hf-model:KRAFTON/Raon-1-nano"])[0] == "unknown"
    assert c["hf-model:KRAFTON/Raon-1-nano"]["size"] == ""                                                           # 크기가 분명하지 않으면 뺀다
    assert slots.lang_status(c["hf-model:org/EnOnly-0.6B"])[0] == "english_only" and c["hf-model:org/EnOnly-0.6B"]["size"] == "0.6B"
    assert slots.lang_status(c["hf-model:org/Euro-27B"])[0] == "pass" and c["hf-model:org/Euro-27B"]["size"] == "27B"   # ko 없이 3개 언어
    assert slots.lang_status(c["hf-model:org/Multi-E2B"])[0] == "pass" and c["hf-model:org/Multi-E2B"]["size"] == "E2B"
    assert slots.derive_fields({"title": "Qwen3-30B-A3B", "id": "x"})["size"] == "30B"


def test_judge_input_has_langs_base_size():
    cands = slots.collect(HF_ITEMS)
    line = next(ln for ln in slots.prompt(cands, SLOTS_LANG).splitlines() if "LFM3-1.7B" in ln and ln.startswith("{"))
    d = json.loads(line)
    assert "ko" in d["langs"] and d["base_model"] == "LiquidAI/LFM3-1.7B-Base" and d["size"] == "1.7B", d
    raon = json.loads(next(ln for ln in slots.prompt(cands, SLOTS_LANG).splitlines() if "Raon" in ln and ln.startswith("{")))
    assert "langs" not in raon and "size" not in raon                                                                 # 없는 값은 안 붙인다
    assert "missing" in slots.SYSTEM_V2 and "langs" in slots.SYSTEM_V2


def test_lang_promotes_only_when_language_alone_missing():
    judged, adj = _run_lang({"LiquidAI/LFM3-1.7B": ["language"]})
    m = next(m for m in judged["hf-model:LiquidAI/LFM3-1.7B"]["matches"] if m["slot"] == "medical-ondevice")
    assert m["fit"] == 2 and m["reason"].endswith(" · 언어: HF 태그로 확인(ko) — 코드가 2로 올림"), m
    assert adj == [{"id": "hf-model:LiquidAI/LFM3-1.7B", "slot": "medical-ondevice", "from": 1, "to": 2, "why": "HF 태그로 확인(ko)"}], adj
    for mi in (["runtime"], ["language", "runtime"], [], None):
        judged, adj = _run_lang({"LiquidAI/LFM3-1.7B": mi})
        m = next(m for m in judged["hf-model:LiquidAI/LFM3-1.7B"]["matches"] if m["slot"] == "medical-ondevice")
        assert m["fit"] == 1 and adj == [], (mi, m, adj)
    judged, adj = _run_lang({"LiquidAI/LFM3-1.7B": ["language"]})                    # fit 1이 아니면(3) 건드리지 않는다
    cands = slots.collect(HF_ITEMS)
    j3, _ = slots.validate(_lang_so({"LiquidAI/LFM3-1.7B": ["language"]}, fit=3), cands, SLOTS_LANG)
    assert slots.apply_lang(cands, j3, SLOTS_LANG) == []


def test_lang_english_only_goes_zero_unknown_unchanged_no_lang_slot_untouched():
    judged, adj = _run_lang({"org/EnOnly-0.6B": ["language"], "KRAFTON/Raon-1-nano": ["language"], "org/Euro-27B": ["language"]})
    en = judged["hf-model:org/EnOnly-0.6B"]["matches"]
    assert [m["slot"] for m in en] == ["gate-classifier"], en                           # 0점은 matches에서 빠진다 (lang 없는 자리는 그대로 1)
    assert [(a["id"], a["to"]) for a in adj if a["slot"] == "medical-ondevice"] == [("hf-model:org/EnOnly-0.6B", 0), ("hf-model:org/Euro-27B", 2)], adj
    assert next(a for a in adj if a["to"] == 0)["why"] == "영어 전용 태그"
    raon = {m["slot"]: m for m in judged["hf-model:KRAFTON/Raon-1-nano"]["matches"]}
    assert raon["medical-ondevice"]["fit"] == 1 and raon["medical-ondevice"]["reason"] == "언어 정보가 없어 한국어 확인 필요"   # unknown은 그대로
    assert all(m["fit"] == 1 and m["reason"] == "다른 자리" for j in judged.values() for m in j["matches"] if m["slot"] == "gate-classifier")
    assert all(a["slot"] != "gate-classifier" for a in adj)


def test_old_raw_without_missing_validates_and_lang_adjusted_recorded():
    cands = slots.collect(ITEMS)
    got, dropped = slots.validate(judged_stub_terra(), cands, SLOTS)                  # 옛 답에는 missing이 없다
    assert dropped["bad_fit"] == 1 and all(m["missing"] == [] for j in got.values() for m in j["matches"])
    with tempfile.TemporaryDirectory() as d:
        for body, ok in (("slots:\n- {id: a}\n", True), ("slots:\n- {id: a, lang: ko}\n", True), ("slots:\n- {id: a, lang: KO}\n", False),
                         ("slots:\n- {id: a, lang: kor}\n", False)):
            f = Path(d) / "s.yaml"
            f.write_text(body, encoding="utf-8")
            try:
                slots.load_slots(f)
                got_ok = True
            except slots.SlotError:
                got_ok = False
            assert got_ok == ok, body
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "raw").mkdir()
        slots.write_json(Path(d) / "raw" / "slots-raw.json", {"structured_output": _lang_so({"LiquidAI/LFM3-1.7B": ["language"]})})
        res = slots.run(HF_ITEMS, SLOTS_LANG, engine="stub", out=Path(d) / "raw", reuse=True)
    assert [(a["id"], a["to"]) for a in res["lang_adjusted"]] == [("hf-model:LiquidAI/LFM3-1.7B", 2)], res["lang_adjusted"]
    assert "hf-model:LiquidAI/LFM3-1.7B" in res["to_detect"]


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_validate, orig_picks, orig_system_for, orig_fold = slots.validate, detect_feed.slot_picks, slots.system_for, slots.fold_same_model

    def trust(so, cands, sl):     # 모델 답을 검사 없이
        return {j["id"]: {"release": j["release"], "matches": j["matches"]} for j in so["judgments"]}, {"unknown_id": 0, "unknown_slot": 0, "bad_fit": 0}

    def firsts_only(sl):          # 자리마다 1위만 detect로 (11/4 오전 규칙)
        return {xs[0]["id"]: (s, xs[0]) for s, xs in ((sl or {}).get("per_slot") or {}).items() if xs}

    orig_apply, orig_derive = slots.apply_lang, slots.derive_fields
    orig_status, orig_prompt = slots.lang_status, slots.prompt

    def promote_any(cands, judged, sl):       # missing 조건을 안 본다
        for j in judged.values():
            for m in j["matches"]:
                if m["slot"] == "medical-ondevice" and m["fit"] == 1:
                    m["fit"] = 2
        return orig_apply(cands, judged, sl)

    def no_lang_prompt(cands, sl):            # 판정 입력에서 langs 등을 뺀다
        return orig_prompt([{k: v for k, v in c.items() if k not in ("langs", "base_model", "size")} for c in cands], sl)

    def english_as_pass(c):
        st = orig_status(c)
        return ("pass", "en") if st[0] == "english_only" else st

    def unknown_as_pass(c):
        st = orig_status(c)
        return ("pass", "?") if st[0] == "unknown" else st

    orig_load = slots.load_slots

    def lenient_load(path):                   # lang 검사 없이
        import yaml
        d = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return {"version": d.get("version"), "slots": d["slots"]}

    return [
        ("문턱 없음 (fit 1도 목록에)", lambda: setattr(slots, "FIT_MIN", 0), lambda: setattr(slots, "FIT_MIN", 2)),
        ("모델 답을 검사 없이 믿음", lambda: setattr(slots, "validate", trust), lambda: setattr(slots, "validate", orig_validate)),
        ("API 키를 안 지움", lambda: setattr(slots, "STRIP_ENV", ()), lambda: setattr(slots, "STRIP_ENV", ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))),
        ("자리마다 1위만 detect로", lambda: setattr(detect_feed, "slot_picks", firsts_only), lambda: setattr(detect_feed, "slot_picks", orig_picks)),
        ("HN 점수 문턱 없음", lambda: setattr(slots, "HN_MIN_POINTS", 0), lambda: setattr(slots, "HN_MIN_POINTS", 100)),
        ("v2 프로필인데 v1 지시문 (지금 방식이 아니면 깎는다)", lambda: setattr(slots, "system_for", lambda s: slots.SYSTEM),
         lambda: setattr(slots, "system_for", orig_system_for)),
        ("넓은 후보 끔 (HF 작업별 순위 · LocalLLaMA 출시 낱말 없는 글)", lambda: setattr(slots, "WIDE_CANDIDATES", False),
         lambda: setattr(slots, "WIDE_CANDIDATES", True)),
        ("같은 모델을 묶지 않음", lambda: setattr(slots, "fold_same_model", lambda xs: xs), lambda: setattr(slots, "fold_same_model", orig_fold)),
        ("판정 입력에 langs · base_model · size를 안 붙임", lambda: setattr(slots, "prompt", no_lang_prompt), lambda: setattr(slots, "prompt", orig_prompt)),
        ("자리 lang 값 검사 없음", lambda: setattr(slots, "load_slots", lenient_load), lambda: setattr(slots, "load_slots", orig_load)),
        ("언어 보정 없음", lambda: setattr(slots, "apply_lang", lambda c, j, sl: []), lambda: setattr(slots, "apply_lang", orig_apply)),
        ("missing 조건 없이 1점을 2로 올림", lambda: setattr(slots, "apply_lang", promote_any), lambda: setattr(slots, "apply_lang", orig_apply)),
        ("영어 전용도 통과로 봄", lambda: setattr(slots, "lang_status", english_as_pass), lambda: setattr(slots, "lang_status", orig_status)),
        ("태그 없는(unknown)도 통과로 봄", lambda: setattr(slots, "lang_status", unknown_as_pass), lambda: setattr(slots, "lang_status", orig_status)),
        ("태그에서 언어 · 기반 · 크기를 안 뽑음", lambda: setattr(slots, "derive_fields", lambda it: {"langs": [], "base_model": "", "size": ""}),
         lambda: setattr(slots, "derive_fields", orig_derive)),
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

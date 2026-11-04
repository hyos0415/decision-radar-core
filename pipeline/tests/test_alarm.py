"""경보 그물(pipeline/alarm.py, HYO-170 가 · 나) 오프라인 테스트 — 모델 없이 (stub 관문).

  python pipeline/tests/test_alarm.py

W44 · W45 모양을 재현한다: LiteLLM 1.107.0 PyPI 백도어(HN · Reddit) · Broccoli2Shell(Node.js · Python RCE) · llama.cpp CVE PSA ·
Claude Code 확장 백도어가 레인 B에 묻혔고, 같은 주 PHP2Shell은 우리 스택이 아니었다.
- 그물: 스택 · 런타임 이름 + 사고 낱말 → 관문에도 (이름 힌트로 llama.cpp ↔ llama-cpp) · 같은 사고 글은 하나로 접어 detect
- 관문이 모르는 기술(Claude Code)은 버리지 않고 목록 「경보」 칸 · detect (security_alert · community — R2)
- 「우리 스택 아님」: 사고 낱말 + 인기 높음 + 그물 밖
뒤쪽 변이 검사(규율 3): 런타임 이름을 빼거나 · 흔한 낱말을 이름으로 보거나 · 힌트를 무시하거나 · 모르는 기술을 버리거나 · 접지 않거나 ·
인기 문턱을 없애면 위 테스트가 빨개져야 한다.
"""
import datetime
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import alarm, detect_feed, profile_filter  # noqa: E402
from pipeline import interest_profile as ip  # noqa: E402
from pipeline.importance import gate as gate_mod, policy  # noqa: E402
from pipeline.importance.backends import StubBackend, TechQuery  # noqa: E402
from pipeline.importance.tiers import TierStore  # noqa: E402

STACK = ["litellm", "llama.cpp", "requests", "anthropic"]


def _it(i, source, title, points=None, rank=None):
    signals = {"points": points} if points is not None else ({"rank:r/LocalLLaMA:top-week": rank} if rank is not None else {})
    return {"id": i, "source": source, "title": title, "url": f"https://example.com/{i}", "text": "", "channels": ["stories"],
            "signals": signals, "meta": {}, "created_at": "2026-10-21T00:00:00Z"}


ITEMS = [
    _it("hn:litellm", "hn", "Compromised LiteLLM 1.107.0 on PyPI Installs a Credential Stealer and Backdoor", 596),
    _it("reddit:litellm", "reddit", "PSA: litellm 1.107.0 on PyPI and Docker shipped an infostealer and backdoor", rank=11),
    # W44 실제 id 순서: 본문 없는 트윗(186점)이 id로 앞 — id 순 대표면 이게 detect로 가고 400점 글이 접혔다
    _it("hn:50216791", "hn", "Evolving Threat: http2shell is an RCE in Node, Python, and more", 186),
    _it("hn:broccoli", "hn", "Broccoli2Shell: Brotli decoder RCE in Node.js, Python and more", 400),
    _it("reddit:llamacve", "reddit", "PSA: llama.cpp CVE-2026-116096", rank=19),
    _it("hn:50000001", "hn", "Claude Code CVE-2026-0001: prompt file path traversal", 90),
    _it("hn:cc", "hn", "Backdoors in official Claude Code extensions", 662),
    _it("hn:k8s", "hn", "Attackers compromised a Kubernetes node via kubelet", 40),
    _it("hn:php", "hn", "Tell HN: I unknowingly broke the PHP RCE embargo. I am sorry", 751),
    _it("reddit:proxy", "reddit", "Reverse proxy for LLMs which can shut down your inference server in response to requests", rank=25),
    _it("hn:feature", "hn", "LiteLLM 1.108 adds streaming for every provider", 150),
    _it("hn:kernel", "hn", "Wanted to download an ISO, ended up writing a kernel exploit", 50),
]
NET_IDS = {"hn:litellm", "reddit:litellm", "hn:50216791", "hn:broccoli", "reddit:llamacve", "hn:50000001", "hn:cc"}


def _store():
    st = TierStore()
    tiers = {"litellm": ["L5"], "llama-cpp": ["L1"], "python": ["L0", "L1"], "anthropic": ["L5"]}
    be = StubBackend(tiers=tiers)
    for name in tiers:
        st.classify(TechQuery(name=name, usage="u", signals={}), be)
    return st


def _profile():
    p, errors, _ = ip.parse_profile({"version": 1, "interests": [{"topic": "하이브리드 검색", "description": "d"}]}, datetime.date(2026, 11, 4))
    assert not errors
    return p


def _run():
    g = gate_mod.Gate(StubBackend(changes={i: ["security"] for i in NET_IDS}), _store())
    return profile_filter.run_filter(ITEMS, _profile(), ["kw"], STACK, gate=g)


def test_net_catches_incidents_and_skips_noise():
    hits = {it["id"]: m for it, m in alarm.net(ITEMS, STACK)}
    assert set(hits) == NET_IDS, sorted(hits)
    assert "Node.js" in hits["hn:broccoli"] and "Claude Code" in hits["hn:cc"]        # 런타임 · 도구 이름
    assert hits["hn:50216791"] == ["Python", "Node.js"], hits["hn:50216791"]           # 줄임 이름 Node → Node.js (한 번만)
    assert hits["hn:broccoli"].count("Node.js") == 1                                    # 「Node.js」에 줄임 이름이 또 붙지 않는다
                                                                                         # 「Kubernetes node」(소문자)는 안 걸린다 — NET_IDS 밖
    no = alarm.not_ours(ITEMS, set(hits))
    assert [x["id"] for x in no] == ["hn:php"], no                                       # 인기 문턱 아래(kernel 50점) · 사고 낱말 없음(feature)은 빠진다


def test_run_filter_gates_net_items_with_name_hint():
    res = _run()
    a = {e["id"]: e for e in res["lane_a"]}
    assert set(a) == NET_IDS and all(e["alarm"] for e in a.values())                   # 레인 A가 아니라 그물에서 온 것만
    assert res["counts"]["lane_a"] == 0 and res["counts"]["alarm_net"] == 7
    assert a["reddit:llamacve"]["gate"]["technologies"] == ["llama-cpp"] and a["reddit:llamacve"]["gate"]["keep"]   # 힌트: llama.cpp ↔ llama-cpp
    assert a["hn:broccoli"]["gate"]["keep"] and "python" in a["hn:broccoli"]["gate"]["technologies"]
    assert a["hn:cc"]["gate"]["status"] == "unclassified" and not a["hn:cc"]["gate"]["keep"]                        # 등급 파일에 없는 도구
    s = res["gate_summary"]
    assert s["folded"] == {"reddit:litellm": "hn:litellm", "hn:50216791": "hn:broccoli"}, s["folded"]   # 같은 기술 경보는 하나로 · 대표는 인기 순
    assert {x["id"] for x in s["shown"]} == {"hn:litellm", "hn:broccoli", "reddit:llamacve"}, s["shown"]
    assert s["status_counts"]["lane_a"] == 0                                             # 판정 못 한 비율 경고는 레인 A만
    assert [x["id"] for x in res["alarm"]["not_ours"]] == ["hn:php"]


def test_detect_feed_lists_alarm_unknown_and_not_ours():
    res = _run()
    with tempfile.TemporaryDirectory() as d:
        snap, run, out = Path(d) / "snap", Path(d) / "run", Path(d) / "out"
        snap.mkdir(), run.mkdir()
        (snap / "items.jsonl").write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in ITEMS), encoding="utf-8")
        (run / "lane_a.json").write_text(json.dumps(res["lane_a"], ensure_ascii=False), encoding="utf-8")
        (run / "alarm.json").write_text(json.dumps(res["alarm"], ensure_ascii=False), encoding="utf-8")
        (run / "run.json").write_text(json.dumps({"snapshot": str(snap), "counts": res["counts"],
                                                  "lane_a_gate": {"model": "stub", **res["gate_summary"]}}, ensure_ascii=False), encoding="utf-8")
        f = detect_feed.build(run, out)
        rows = json.loads((out / "feed.json").read_text(encoding="utf-8"))
        feed = {e["id"]: (e["kind"], e["source_type"]) for e in rows}
        assert feed == {i: ("security_alert", "community") for i in ("hn:litellm", "hn:broccoli", "reddit:llamacve", "hn:cc")}, feed
        # 접힌 글은 대표의 detect 입력(요약 · 전문 파일)에 제목 · 링크로 붙는다 — 다른 사고여도 detect가 본다
        files = {k: v["file"] for k, v in json.loads((out / "index.json").read_text(encoding="utf-8")).items()}
        broccoli = next(e for e in rows if e["id"] == "hn:broccoli")
        body = (out / files[detect_feed.url_key(broccoli["url"])]).read_text(encoding="utf-8")
        assert "http2shell" in broccoli["summary"] and "https://example.com/hn:50216791" in body, body
        cc = next(e for e in rows if e["id"] == "hn:cc")
        assert "CVE-2026-0001" in cc["summary"]                                         # 관문이 모르는 기술 묶음도 같다
        assert "같은 기술의 다른 경보" not in next(e for e in rows if e["id"] == "reddit:llamacve")["summary"]
        lst = json.loads((out / "list.json").read_text(encoding="utf-8"))
        assert [(x["id"], x["to_detect"], x["folded_into"]) for x in lst["alarm"]] == [("hn:50000001", False, "hn:cc"), ("hn:cc", True, None)], lst["alarm"]
        assert [x["id"] for x in lst["not_ours"]] == ["hn:php"]
        always = {x["id"]: x for x in lst["always"]}
        assert always["reddit:litellm"]["folded_into"] == "hn:litellm" and always["hn:litellm"]["alarm"]
        assert always["hn:50216791"]["folded_into"] == "hn:broccoli"
        assert f["alarm"] == {"net": 7, "kept": 5, "unknown": 2, "not_ours": 1}, f["alarm"]
        assert "경보 그물 7" in detect_feed.funnel_line(f)


def test_retired_technology_stays_quiet():
    # LiteLLM 기각(2026-11-04): 등급 파일 사람 수정에 retired → PyPI 이름으로 그물에 걸려도 관문이 「스택에서 뺀 기술」로 빼고 경보 칸에도 안 간다
    st = _store()
    st.data["overrides"]["litellm"] = {"technology": "litellm", "retired": "2026-11-04 기각"}
    g = gate_mod.Gate(StubBackend(changes={i: ["security"] for i in NET_IDS}), st)
    res = profile_filter.run_filter(ITEMS, _profile(), ["kw"], [t for t in STACK if t != "litellm"], gate=g)
    a = {e["id"]: e for e in res["lane_a"]}
    assert a["hn:litellm"]["gate"]["status"] == "retired" and not a["hn:litellm"]["gate"]["keep"], a["hn:litellm"]["gate"]
    assert "hn:litellm" not in {x["id"] for x in res["gate_summary"]["shown"]}
    assert [e["id"] for e, _ in detect_feed.alarm_unknown(res["lane_a"])] == ["hn:50000001", "hn:cc"]   # 경보 칸은 Claude Code만
    assert res["gate_summary"]["status_counts"]["lane_a"] == 0                                  # 그물 글은 레인 A 비율에 안 든다


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_resolve, orig_unknown, orig_fold = gate_mod.resolve_technologies, detect_feed.alarm_unknown, policy.fold_key

    def no_hint(item, names, vendor_terms=None):
        meta = {k: v for k, v in (item.get("meta") or {}).items() if k != "alarm_terms"}
        return orig_resolve(dict(item, meta=meta), names, vendor_terms)

    return [
        ("런타임 · 도구 이름 없음", lambda: setattr(alarm, "RUNTIME_TERMS", ()), lambda: setattr(alarm, "RUNTIME_TERMS", orig_runtime)),
        ("흔한 낱말(requests)도 이름으로", lambda: setattr(alarm, "COMMON_WORDS", set()), lambda: setattr(alarm, "COMMON_WORDS", {"requests"})),
        ("관문이 그물 이름 힌트를 무시", lambda: setattr(gate_mod, "resolve_technologies", no_hint), lambda: setattr(gate_mod, "resolve_technologies", orig_resolve)),
        ("관문이 모르는 기술은 버림", lambda: setattr(detect_feed, "alarm_unknown", lambda la: []), lambda: setattr(detect_feed, "alarm_unknown", orig_unknown)),
        ("같은 사고 글을 접지 않음", lambda: setattr(policy, "fold_key", lambda e: ("item", e["id"])), lambda: setattr(policy, "fold_key", orig_fold)),
        ("「우리 스택 아님」 인기 문턱 없음", lambda: setattr(alarm, "NOT_OURS_HN_POINTS", 0), lambda: setattr(alarm, "NOT_OURS_HN_POINTS", 100)),
        ("경보 대표를 id 순으로", lambda: setattr(alarm, "popularity", lambda it: (0,)), lambda: setattr(alarm, "popularity", orig_popularity)),
        ("접힌 경보를 detect 입력에 안 붙임", lambda: setattr(detect_feed, "kin_note", lambda items: ""), lambda: setattr(detect_feed, "kin_note", orig_kin_note)),
        ("줄임 이름(Node) 없음", lambda: setattr(alarm, "CASED_ALIASES", {}), lambda: setattr(alarm, "CASED_ALIASES", {"Node": "Node.js"})),
        ("줄임 이름을 대소문자 무시로", lambda: setattr(alarm, "ALIAS_FLAGS", re.I), lambda: setattr(alarm, "ALIAS_FLAGS", 0)),
    ]


orig_runtime, orig_popularity, orig_kin_note = alarm.RUNTIME_TERMS, alarm.popularity, detect_feed.kin_note


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
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
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

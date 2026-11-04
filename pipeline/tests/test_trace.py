"""탈락 추적(pipeline/trace.py, HYO-171) 오프라인 테스트 — 「놓친 것」 5건의 회귀 정답. 모델 호출 없음.

  python pipeline/tests/test_trace.py

픽스처 `fixtures/missed/`는 실제 기록에서 뽑았다 (make_fixture.py): 얼린 코퍼스의 그 항목들 · 그 주에 실제로 돈 관문 판정(「그때」) ·
11/4 지금 코드로 돌린 자리 질의 결과(`now/`, 「지금」). 기록 문서: docs/decisions/2026-11-04-missed-items.md.

  사례                         그때 (그 주 실제 run)                              지금 (지금 코드 재생)
  GPT-6.1 Terra  W44 changelog  묶음으로 판정 → luna 가격 줄로 남김 → 5/31에서 잘림    블록 하나 → 4 자리 질의 2위 → detect
                 W44 · W45 Bedrock  2c minor·patch 버림                        2c 버림 · 4 적합도 1 (그대로 — 기록만)
                 W45 HN 637점   레인 B 막다른 길                                 4 자리 질의 1위 → detect
  Claude Haiku 5.5  W42 HN 1047점  관문 run 없음 (주간 사슬 전)                    4 자리 질의 → detect
  LiteLLM 1.107.0 백도어 W44 HN  레인 B — 경보 그물 전                              경보 그물 → 2a 스택에서 뺀 기술 (맞는 결과)
  http2shell  W44 HN           레인 B — 경보 그물 전                              경보 그물 → python L0 × security → 「항상」 → detect
  Broccoli2Shell  W44 HN       레인 B — 경보 그물 전                              경보 그물 → … → 「항상」 python으로 http2shell에 접힘 (목록에만)

재생의 모델 답은 그 주 lane_a.json에 기록된 것만 쓴다. 기록이 없는 경보 그물 글(http2shell · Broccoli2Shell — 그 주에는 그물이 없었다)은
security로 짚는다고 가정한다(ASSUMED). 뒤쪽 변이 검사(규율 3): 쪼개기 · 자리 질의 detect 몫 · 경보 그물 · 스택에서 뺀 기술 ·
trace의 칸 정리 · 자리 질의 기록 · 재생의 묶음 답 빼기를 망가뜨리면 위 테스트가 빨개져야 한다.
"""
import contextlib
import datetime
import io
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline import alarm, detect_feed, page_entries, profile_filter, trace  # noqa: E402
from pipeline import interest_profile as ip  # noqa: E402
from pipeline.importance import gate as gate_mod  # noqa: E402
from pipeline.importance.cli import load_vendor_terms  # noqa: E402
from pipeline.importance.tiers import TierStore  # noqa: E402

FX = Path(__file__).resolve().parent / "fixtures" / "missed"
NOW_STACK = ["openai", "openai-agents", "gpt-6-luna", "gpt-6-sol", "Amazon Bedrock", "python"]   # 11/4 — litellm은 스택에서 뺐다 (#258)
ASSUMED = {"hn:50216791": ["security"], "hn:50219177": ["security"]}                            # 기록 없음 — 가정

TERRA_BLOCK = "vendor-diff:openai:https://platform.openai.com/docs/changelog@2026-W44#oct-26"
TERRA_PARENT = "vendor-diff:openai:https://platform.openai.com/docs/changelog@2026-W44"
BEDROCK_TERRA = "vendor:aws-bedrock:0c5c946db9a84a5a290bb7e1c3b4577820eb630a"
HAIKU_HN, TERRA_HN = "hn:49996437", "hn:50248124"
LITELLM_HN, HTTP2SHELL, BROCCOLI = "hn:50181114", "hn:50216791", "hn:50219177"


def _profile():
    p, errors, _ = ip.parse_profile({"version": 1, "interests": [{"topic": "하이브리드 검색", "description": "d"}]}, datetime.date(2026, 11, 4))
    assert not errors
    return p


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).exists() else None


def then(week):
    """그 주에 실제로 돈 run으로 만든 trace (자리 질의 없음 — 그때는 없었다)."""
    d = FX / week
    items = profile_filter.read_items(d)
    has_run = (d / "run.json").exists()
    return {r["id"]: r for r in trace.from_run(items, d if has_run else None, d if (d / "feed.json").exists() else None,
                                                 slots_path=d / "none.json")}


def now(week):
    """지금 코드 재생 — 기록된 모델 답 + ASSUMED, 자리 질의는 11/4 결과 (now/)."""
    d = FX / week
    items = profile_filter.read_items(d)
    rec = trace.recorded_labels(_load(d / "lane_a.json") or [], items)
    rec.update(ASSUMED)
    g = gate_mod.Gate(trace.replay_backend(rec), TierStore(str(FX / "tiers.json")), load_vendor_terms())
    recs = trace.replay(items, g, _profile(), NOW_STACK, slots=_load(d / "now" / "slots.json"), slots_raw=_load(d / "now" / "slots-raw.json"))
    return {r["id"]: r for r in recs}


def at(rec, stage):
    s = [x for x in rec["steps"] if x["stage"] == stage]
    assert s, (rec["id"], stage, [x["stage"] for x in rec["steps"]])
    return s[0]


def expect(rec, stage, ok, *words):
    s = at(rec, stage)
    assert s["pass"] is ok, (rec["id"], stage, s)
    for w in words:
        assert w in s["note"], (rec["id"], stage, w, s["note"])


# ---------------------------------------------------------------------------- 「놓친 것」 5건

def test_terra_w44_changelog():
    t = then("2026-W44")[TERRA_BLOCK]
    expect(t, "1", True, "묶음 항목", TERRA_PARENT)            # 쪼개기 전 — 묶음으로 판정
    expect(t, "2c", True, "gpt-6-luna", "가격")                 # Terra가 아니라 luna 줄로 남겼다
    expect(t, "3", False, "5/31")                                # 5칸 상한에서 잘림 (HYO-147 · HYO-168 전)
    assert t["end"] == "out" and [s["stage"] for s in t["stops"]] == ["3"], t["stops"]
    n = now("2026-W44")[TERRA_BLOCK]
    expect(n, "1", True, "page-diff:openai")
    assert "묶음" not in at(n, "1")["note"]                      # 지금은 블록 하나가 항목 하나
    expect(n, "2b", False, trace.REPLAY_MISSING)                 # 블록 단위 모델 답은 기록이 없다 — 재생은 모델을 안 부른다
    expect(n, "4", True, "관문 분류 2위")                          # 자리 질의가 건진다
    expect(n, "5", True, "model_release")
    assert n["end"] == "detect"


def test_terra_bedrock_stays_out():
    for week in ("2026-W44", "2026-W45"):
        t, n = then(week)[BEDROCK_TERRA], now(week)[BEDROCK_TERRA]
        expect(t, "2c", False, "minor·patch")
        expect(n, "2c", False, "minor·patch")
        expect(n, "4", False, "적합도 2 이상인 자리 없음")          # 의료 운영 LLM 적합도 1 — 서울 리전 확인 안 됨
        assert t["end"] == n["end"] == "out"


def test_terra_w45_hn():
    t = then("2026-W45")[TERRA_HN]
    expect(t, "1", None, "레인 B")
    expect(t, "B", None, "순위 밖")
    assert t["end"] == "out" and [s["stage"] for s in t["stops"]] == ["B"], t["stops"]     # 레인 B 막다른 길
    assert not [s for s in t["steps"] if s["stage"] == "4"]                                 # 그 주에는 자리 질의가 없었다
    n = now("2026-W45")[TERRA_HN]
    expect(n, "4", True, "관문 분류 1위")
    assert n["end"] == "detect"


def test_haiku_w42():
    t = then("2026-W42")[HAIKU_HN]
    expect(t, "1", None, "관문 run이 없다")
    assert t["end"] == "unknown"                                  # W42는 주간 사슬 전 — detect 입력이 없다
    n = now("2026-W42")[HAIKU_HN]
    expect(n, "4", True, "조건 추출 2위", "적합도 3")
    assert n["end"] == "detect"


def test_litellm_backdoor():
    t = then("2026-W44")[LITELLM_HN]
    expect(t, "net", False, "경보 그물이 없었다", "litellm")      # 그때 run은 HYO-170 전 — 지금이라면 걸린다고 알린다
    expect(t, "B", None, "순위 밖")
    assert t["end"] == "out"
    n = now("2026-W44")[LITELLM_HN]
    expect(n, "net", True, "PyPI")                                # litellm은 스택 이름에서 빠졌어도 PyPI로 걸린다
    expect(n, "2a", False, "스택에서 뺀 기술")                     # 맞는 결과 — 11/4 기각
    assert n["end"] == "out" and n["stops"][0]["stage"] == "2a"


def test_http2shell():
    t = then("2026-W44")[HTTP2SHELL]
    expect(t, "net", False, "경보 그물이 없었다")
    n = now("2026-W44")[HTTP2SHELL]
    expect(n, "net", True, "python")                              # 제목 「Node」는 Node.js 이름에 안 걸린다 — Python으로만
    expect(n, "2a", True, "python L0")
    expect(n, "2c", True, "python L0 × security")
    expect(n, "3", True, "「항상」")
    expect(n, "5", True, "security_alert")
    assert n["end"] == "detect"


def test_broccoli2shell_folds_into_http2shell():
    t = then("2026-W44")[BROCCOLI]
    expect(t, "net", False, "경보 그물이 없었다", "Node.js")
    n = now("2026-W44")[BROCCOLI]
    expect(n, "net", True, "Node.js")
    expect(n, "2c", True, "security")
    expect(n, "3", False, "접힘", HTTP2SHELL)                     # 경보 접기 열쇠가 (alarm, 기술) — 다른 사고라도 python이면 한 건
    assert n["end"] == "list"


# ---------------------------------------------------------------------------- 배관

def test_replay_drops_stale_bundle_answer():
    n = now("2026-W44")[TERRA_PARENT]                              # 쪼갠 뒤 남은 머리 줄 — 묶음 전체의 예전 답(pricing)을 쓰지 않는다
    expect(n, "2b", False, trace.REPLAY_MISSING)


def test_alarm_unknown_folded_is_list_only():
    # W45 모양: Claude Code 확장 백도어 글 둘 — 관문이 모르는 기술(등급 파일에 없다)이라 「경보」 칸, 같은 이름 묶음은 처음 글만 detect
    def it(i, title, pts):
        return {"id": i, "source": "hn", "title": title, "url": f"https://example.com/{i}", "text": "", "channels": ["stories"],
                "signals": {"points": pts}, "meta": {}}
    items = [it("hn:cc1", "Backdoor in Claude Code for VS Code extension on the Cursor Marketplace", 1008),
             it("hn:cc2", "Backdoors in official Claude Code extensions", 662)]
    g = gate_mod.Gate(trace.replay_backend({}), TierStore(str(FX / "tiers.json")), load_vendor_terms())
    r = {x["id"]: x for x in trace.replay(items, g, _profile(), NOW_STACK)}
    expect(r["hn:cc1"], "3", True, "「경보」")
    assert r["hn:cc1"]["end"] == "detect"
    expect(r["hn:cc2"], "3", False, "접힘", "hn:cc1")
    assert r["hn:cc2"]["end"] == "list" and not [s for s in r["hn:cc2"]["steps"] if s["stage"] == "5"]


def test_detect_feed_writes_same_trace():
    d = FX / "2026-W44"
    items = profile_filter.read_items(d)
    rec = trace.recorded_labels(_load(d / "lane_a.json"), items)
    rec.update(ASSUMED)
    g = gate_mod.Gate(trace.replay_backend(rec), TierStore(str(FX / "tiers.json")), load_vendor_terms())
    res = profile_filter.run_filter(items, _profile(), ["kw"], NOW_STACK, gate=g)
    with tempfile.TemporaryDirectory() as tmp:
        run, out, snap = Path(tmp) / "run", Path(tmp) / "out", Path(tmp) / "snap"
        run.mkdir(), snap.mkdir()
        shutil.copy(d / "items.jsonl", snap / "items.jsonl")
        for name, obj in (("lane_a.json", res["lane_a"]), ("alarm.json", res["alarm"]), ("lane_b.kw.json", res["lane_b"]["kw"]),
                          ("run.json", {"snapshot": str(snap), "counts": res["counts"], "stack": {"terms": NOW_STACK},
                                        "lane_a_gate": {"model": "replay", **res["gate_summary"]}})):
            (run / name).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        shutil.copy(d / "now" / "slots.json", run / "slots.json")
        shutil.copy(d / "now" / "slots-raw.json", run / "slots-raw.json")
        detect_feed.build(run, out)
        written = {r["id"]: r for r in trace.read(out / trace.TRACE, items)}
        # 리뷰 11/4: 추적이 죽어도 detect 입력은 그대로 나온다 (보조 기록이 주간 사슬을 멈추지 않게)
        orig, out2 = trace.build, Path(tmp) / "out2"
        trace.build = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("trace boom"))
        try:
            f2 = detect_feed.build(run, out2)
        finally:
            trace.build = orig
        assert (out2 / "feed.json").read_text(encoding="utf-8") == (out / "feed.json").read_text(encoding="utf-8")
        assert "trace boom" in f2["trace_error"] and not (out2 / trace.TRACE).exists()
        assert "trace boom" in json.loads((out2 / "funnel.json").read_text(encoding="utf-8"))["trace_error"]
    assert len(written) == len(items)                              # 항목마다 한 줄
    want = now("2026-W44")
    for i in (TERRA_BLOCK, HTTP2SHELL, BROCCOLI, LITELLM_HN):
        assert written[i]["end"] == want[i]["end"], (i, written[i]["end"], want[i]["end"])
        assert written[i] == want[i], i                                                  # 짧게 써도 읽으면 같은 기록


def test_command_search_and_not_collected():
    def run(*args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = trace.main(list(args) + ["--snapshots", str(FX), "--run", str(FX / "2026-W44"), "--detect", str(FX / "2026-W44")])
        return rc, buf.getvalue()
    rc, out = run("http2shell", "--week", "2026-W44")
    assert rc == 0 and HTTP2SHELL in out and "경보 그물" in out and "= 빠짐" in out, out
    rc, out = run("terra", "oct-26", "--week", "2026-W44")
    assert rc == 0 and out.count("[2026-W44]") == 1 and "5/31" in out, out        # 낱말은 전부 들어야 맞는다
    rc, out = run("Mistral", "Large", "--week", "2026-W44")
    assert rc == 0 and "수집 안 됨" in out, out
    rc, out = run("Haiku", "--week", "2026-W44", "--json")
    assert rc == 0 and "수집 안 됨" in out


TESTS = [(n, f) for n, f in globals().items() if n.startswith("test_")]


def mutations():
    orig_expand, orig_picks, orig_net = page_entries.expand, detect_feed.slot_picks, alarm.net
    orig_eff, orig_cap, orig_slot, orig_rec = TierStore.effective, trace.cap_step, trace.slot_step, trace.recorded_labels

    def no_retired(self, name):
        e = orig_eff(self, name)
        return None if e is None else {k: v for k, v in e.items() if k != "retired"}

    return [
        ("changelog 쪼개기 끔", lambda: setattr(page_entries, "expand", lambda items: list(items)), lambda: setattr(page_entries, "expand", orig_expand)),
        ("자리 질의 detect 몫 없음", lambda: setattr(detect_feed, "slot_picks", lambda s: {}), lambda: setattr(detect_feed, "slot_picks", orig_picks)),
        ("경보 그물 끔", lambda: setattr(alarm, "net", lambda lane_b, terms: []), lambda: setattr(alarm, "net", orig_net)),
        ("스택에서 뺀 기술을 판정", lambda: setattr(TierStore, "effective", no_retired), lambda: setattr(TierStore, "effective", orig_eff)),
        ("trace: 칸 정리를 늘 통과로", lambda: setattr(trace, "cap_step", lambda e, c: trace.step("3", True, "?")), lambda: setattr(trace, "cap_step", orig_cap)),
        ("trace: 자리 질의 기록 안 함", lambda: setattr(trace, "slot_step", lambda it, si: None), lambda: setattr(trace, "slot_step", orig_slot)),
        ("재생: 쪼갠 묶음의 예전 답을 씀", lambda: setattr(trace, "recorded_labels", lambda la, items=(): orig_rec(la)),
         lambda: setattr(trace, "recorded_labels", orig_rec)),
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

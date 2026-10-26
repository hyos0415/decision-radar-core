"""제품 eval 진입 — 53케이스 전부(revisit 35 · capture 11 · detect 7)를 스텁 또는 실제 모델로 돈다.

채점기는 한 벌이다 (비교 프로토콜 §4·§9): revisit·detect는 eval/runner/run_case.grade(→ grade_detect), capture는 eval/runner/capture.grade.
제품용 채점기를 따로 만들지 않았다. 여기 있는 것은 실행 쪽뿐이다:
  - revisit·capture 실행은 spike/common/eval_adapter(run_revisit·run_capture)를 그대로 쓴다 — 격리(runroot 사본 radar.py)·P3~P6·2턴 프로세스 재개가 거기 있다
  - detect 실행(run_detect)은 같은 prepare·finish를 쓰고 새로 만들었다
  - 호출 단위 사용량(llm_call)은 transcript에 들어 있어서 usage.py가 읽는다

스텁 통과는 **배관 검증일 뿐이다** — 모델의 판단은 재지 않는다 (README).
"""

import argparse
import asyncio
import datetime as dt
import glob
import json
import os
import sys

from common import eval_adapter as ea
from common.core import Ctx
from common.guard import CountingProcessor, NetGuard

from . import script as S
from . import usage
from .runtime import Runtime

rc = ea.rc


def all_case_ids():
    out = []
    for f in sorted(glob.glob(os.path.join(rc.EVAL, "cases", "batch-*.jsonl"))):
        out += [json.loads(l)["id"] for l in open(f, encoding="utf-8") if l.strip()]
    return out


# ── detect 1회 ───────────────────────────────────────────────────────

def run_detect(rt, case, spec, out_dir, radar_from_repo=False):
    work, before, radar_py, env = ea.prepare(case, radar_from_repo)
    ctx = Ctx.from_env(env, radar_py=radar_py)
    now = (case.get("setup") or {}).get("now", rc.NOW)
    shapes = rt.new_shapes(spec)

    tp = CountingProcessor.install()
    t0 = dt.datetime.now()
    crash = None
    with NetGuard(ea.hosts(spec)) as ng:
        try:
            report, diag = asyncio.run(rt.detect(ctx, rt.make_model(spec, shapes, "detect"), now, rt.settings(spec)))
        except Exception as e:  # 파이프라인 자체가 죽으면 run_report 없음 → 채점에서 떨어진다
            report, diag, crash = None, {"p3_errors": []}, f"{type(e).__name__}: {e}"
    wall = (dt.datetime.now() - t0).total_seconds()

    events = [{"type": "system", "subtype": "init", "model": ea.model_name(spec)}] + ctx.events
    events.append(ea.result_event(report, crash, num_turns=diag.get("model_calls"), duration_ms=int(wall * 1000), total_cost_usd=None))
    ea.write_events(os.path.join(out_dir, "transcript.jsonl"), events)

    g = rc.grade(case, before, work, events)  # → grade_detect — 스킬과 같은 채점기
    g["spike"] = {
        "P3_errors": diag["p3_errors"],
        "P4_radar_copy_identical": ea.sha(radar_py) == ea.sha(ea.SKILL_RADAR),
        "P5_net_attempts": ng.attempts, "P5_net_stacks": ng.stacks,
        "P5_traces": tp.traces, "P5_spans": tp.spans,
        "crash": crash,
        "input_tokens": diag.get("input_tokens"), "output_tokens": diag.get("output_tokens"),
        "model_calls": diag.get("model_calls"),
        "request_shapes": shapes.shapes if shapes else None,
    }
    ea.add_cost(g, spec)
    return ea.finish(g, work, out_dir, wall)


def with_usage(g, out_dir, root, label):
    """호출 단위 기록을 모아 grade.json에 붙이고 실행 루트의 llm_calls.jsonl에 이어 쓴다."""
    calls = usage.run_calls(out_dir)
    g["usage"] = usage.totals(calls)
    json.dump(g, open(os.path.join(out_dir, "grade.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(os.path.join(root, "llm_calls.jsonl"), "a", encoding="utf-8") as f:
        for c in calls:
            f.write(json.dumps({"run": label, **c}, ensure_ascii=False) + "\n")
    return g


def run_one(rt, case, spec_stub, real, out, **kw):
    """케이스 한 번. spec_stub는 스텁 spec(스크립트 포함), real이 None이 아니면 실제 모델."""
    flow = case["flow"]
    spec = real if real is not None else spec_stub
    if flow == "capture":
        return ea.run_capture(rt, case, spec, out, **{k: v for k, v in kw.items() if k == "gate"})
    if flow == "detect":
        return run_detect(rt, case, spec, out, **{k: v for k, v in kw.items() if k == "radar_from_repo"})
    return ea.run_revisit(rt, case, spec, out, **{k: v for k, v in kw.items() if k == "radar_from_repo"})


def stub_spec(case):
    cid = case["id"]
    if case["flow"] == "capture":
        return {"kind": "stub", "script": S.capture_script(cid)}
    if case["flow"] == "detect":
        return {"kind": "stub", "script": S.detect_script()}
    return {"kind": "stub", "script": S.revisit_script(S.SCRIPT["cases"].get(cid, {}))}


def controls():
    """(이름, 설정, flow) — 음성 대조. 채점기가 무력하지 않다는 것을 보인다 (CLAUDE.md 규율 3)."""
    return ([(n, c, "revisit") for n, c in S.SCRIPT["controls"].items()]
            + [(n, c, "capture") for n, c in S.SCRIPT["capture_controls"].items()]
            + [(n, c, "detect") for n, c in S.SCRIPT["detect_controls"].items()])


def run_control(rt, name, c, flow, root):
    out = os.path.join(root, name)
    os.makedirs(out)
    case = rc.load_case(c["case"])
    if flow == "capture":
        sc = S.capture_script(c["script"], c.get("force_propose", False), c.get("draft_from"))
        g = ea.run_capture(rt, case, {"kind": "stub", "script": sc}, out, gate=c.get("gate", True))
    elif flow == "detect":
        g = run_detect(rt, case, {"kind": "stub", "script": S.detect_script(c["script"])}, out, radar_from_repo=c.get("radar") == "repo")
    else:
        g = ea.run_revisit(rt, case, {"kind": "stub", "script": S.revisit_script(c["script"])}, out, radar_from_repo=c.get("radar") == "repo")
    with_usage(g, out, root, name)
    missing = [m for m in c["must_fail"] if m not in ea.failed(g)]
    p3 = [e["kind"] for e in g["spike"]["P3_errors"]]
    ok = not missing and (not c["must_fail"] or not g["pass"]) and (c.get("expect_p3") is None or c["expect_p3"] in p3)
    if c.get("expect_pass"):  # 통과해야 하는 통제 — 가드가 일을 했다는 것을 보인다
        ok = ok and g["pass"] and not p3
    if "expect_llm_calls" in c:
        ok = ok and g["usage"]["calls"] == c["expect_llm_calls"]
    print(json.dumps({"control": name, "flow": flow, "caught": ok, "grade_pass": g["pass"], "failed": ea.failed(g),
                      "expected_but_passed": missing, "p3": p3}, ensure_ascii=False))
    return ok


# ── main ─────────────────────────────────────────────────────────────

def build_parser(rt):
    ap = argparse.ArgumentParser(description="Decision Radar 제품 eval — 53케이스 (LangGraph 비교 모드)")
    ap.add_argument("cases", nargs="*", help="기본: 53케이스 전부 (+ --stub면 음성 대조)")
    ap.add_argument("--stub", action="store_true", help="스텁 모델 (API 없음) — 배관 검증일 뿐이다")
    ap.add_argument("--model", choices=["stub", "anthropic"], default=None, help="anthropic = 실제 Claude (API 과금, .env 키)")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--controls", action="store_true", help="케이스를 지정해도 음성 대조를 돈다 (스텁)")
    rt.add_args(ap)  # --engine api|claude-cli (HYO-99) — 빠져 있어 제품 eval이 늘 API로 돌았다
    return ap


def main(argv=None):
    rt = Runtime()
    a = build_parser(rt).parse_args(argv)
    kind = a.model or ("stub" if a.stub else None)
    if kind is None:
        sys.exit("--stub 또는 --model anthropic")
    if kind != "stub" and not a.cases:
        sys.exit("실제 모델은 케이스를 명시한다 — 과금 (API)")
    stub = kind == "stub"
    real = None if stub else rt.real_spec(a)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-product-{'stub' if stub else rt.label(real)}"
    root = os.path.join(rc.EVAL, "runs", stamp)
    os.makedirs(root)
    p4 = ea.radar_unmodified()
    cases = a.cases or all_case_ids()
    rows = []
    for cid in cases:
        case = rc.load_case(cid)
        for i in range(a.k):
            out = os.path.join(root, f"{cid}-{i + 1}")
            os.makedirs(out)
            g = run_one(rt, case, stub_spec(case) if stub else None, real, out)
            with_usage(g, out, root, f"{cid}-{i + 1}")
            rows.append((case, cid, g))
            print(json.dumps({"case": cid, "flow": case["flow"], "run": i + 1, "pass": g["pass"], "safety_pass": g["safety_pass"],
                              "failed": ea.failed(g), "tool_calls": g["diag"]["tool_calls"], "llm_calls": g["usage"]["calls"]}, ensure_ascii=False))

    ctrl = {}
    if stub and (not a.cases or a.controls):
        for name, c, flow in controls():
            ctrl[name] = run_control(rt, name, c, flow, root)
    ctrl_ok = all(ctrl.values())

    by_flow = {}
    for case, cid, g in rows:
        f = by_flow.setdefault(case["flow"], {"runs": 0, "pass": 0, "failed_cases": {}})
        f["runs"] += 1
        f["pass"] += bool(g["pass"])
        if not g["pass"]:
            f["failed_cases"].setdefault(cid, ea.failed(g))
    pow_cases = sorted({cid for case, cid, _ in rows if case["aggregation"] == "pass_pow_k"})
    graded = [g for _, _, g in rows]
    caps = [(cid, g) for case, cid, g in rows if case["flow"] == "capture"]
    all_calls = usage.summarize(root)
    summary = {
        "runtime": "product/radar (langgraph)", "model": "stub" if stub else rt.settings(real), "runs": root,
        "pass_by_flow": {f: f"{v['pass']}/{v['runs']}" for f, v in sorted(by_flow.items())},
        "pass_total": f"{sum(g['pass'] for g in graded)}/{len(graded)}",
        "failed_cases": {f: v["failed_cases"] for f, v in by_flow.items() if v["failed_cases"]},
        "safety_pass_total": f"{sum(g['safety_pass'] for g in graded)}/{len(graded)}",
        "pass_pow_k_cases": {cid: all(g["pass"] for _, c, g in rows if c == cid) for cid in pow_cases},
        "P3_errors": sum(len(g["spike"]["P3_errors"]) for g in graded),
        "P3_note": "스텁은 P3를 잴 수 없다 — 어댑터 400은 실제 모델에서만" if stub else None,
        "P4": {**p4, "copies_identical": all(g["spike"]["P4_radar_copy_identical"] for g in graded)},
        "P5": {"net_attempts": sum(len(g["spike"]["P5_net_attempts"]) for g in graded),
               "traces": sum(g["spike"]["P5_traces"] for g in graded)},
        "P6": {cid: g["spike"]["P6"]["ok"] for cid, g in caps} or None,
        "usage": {"llm_calls": sum(r["calls"] for r in all_calls), "input_tokens": sum(r["input_tokens"] for r in all_calls),
                  "output_tokens": sum(r["output_tokens"] for r in all_calls), "cost_usd": round(sum(r["cost_usd"] or 0 for r in all_calls), 5),
                  "note": "스텁: 토큰은 글자 수/4 추정, 비용 0" if stub else "API 단가 환산", "per_call": os.path.join(root, "llm_calls.jsonl")},
        "controls": (f"{sum(ctrl.values())}/{len(ctrl)} 잡힘" if ctrl else None),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    ok = (all(g["pass"] for g in graded) and ctrl_ok and p4["ok"] and summary["P5"]["net_attempts"] == 0
          and summary["P5"]["traces"] == 0 and all(g["spike"]["P6"]["ok"] for _, g in caps))
    sys.exit(0 if ok else 1)

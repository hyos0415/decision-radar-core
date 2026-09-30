"""스파이크 eval 어댑터 — 스킬 러너(eval/runner/run_case.py · capture.py)의 build·grade를 그대로 쓰고 실행만 제품 파이프라인으로 바꾼다.

  python product/spike/agents_sdk/run_eval.py --stub                 # 기준 11케이스(revisit 6 + capture 5) + 음성 대조 7 (API 없음)
  python product/spike/agents_sdk/run_eval.py --stub JHS-037 JHS-047 --k 3
  python product/spike/agents_sdk/run_eval.py --model litellm JHS-001 [--no-register]   # 실제 Claude (API 과금, .env 키)

채점기는 한 벌이다 (비교 프로토콜 §4). 제품 trace를 스킬 transcript와 같은 모양으로 만들어 넘긴다.
격리도 같다: radar.py는 작업 디렉터리의 사본(runroot)에서 돌린다 — 레포 경로에 닿는 호출은 answer_key_access에 걸린다.
capture 2턴은 **다른 프로세스**(capture_resume.py)가 상태 파일에서 재개한다 (P6). 사람의 답은 approve()/reject()로 들어간다 —
스킬 러너는 같은 답을 텍스트로 넣는다 (capture.APPROVAL_TEXT). 제품 UI에서는 버튼이다.
산출물: eval/runs/<시각>-agents_sdk-<모델>/<case>-<i>/ (transcript*.jsonl · grade.json · 작업 디렉터리 사본)
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from pipeline import ROOT, SKILL_RADAR, Ctx, revisit  # noqa: E402
from stub_model import CaptureStub, StubModel  # noqa: E402
import capture_flow  # noqa: E402
import models  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "eval", "runner"))
import run_case as rc  # noqa: E402
import capture as cap  # noqa: E402  — 스킬 capture 채점기

from agents import set_trace_processors  # noqa: E402
from guard import CountingProcessor, NetGuard  # noqa: E402

SPIKE_CASES = ["JHS-001", "JHS-016", "JHS-021", "JHS-037", "JHS-043", "JHS-044"]  # runtime-spike.md §2
CAPTURE_CASES = ["JHS-013", "JHS-014", "JHS-039", "JHS-047", "JHS-048"]  # §2 변경 (2026-09-30)
SAFETY_CASES = {"JHS-037", "JHS-043", "JHS-044", "JHS-039", "JHS-047", "JHS-048"}  # P2
SCRIPT = json.load(open(os.path.join(HERE, "stub_script.json"), encoding="utf-8"))
RESUME = os.path.join(HERE, "capture_resume.py")


# ── P4: 규칙층 재사용 ────────────────────────────────────────────────

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def radar_unmodified():
    """radar.py가 커밋된 상태 그대로인가 (작업 트리 diff 0줄). 사본 해시는 실행마다 따로 대조한다."""
    rel = os.path.relpath(SKILL_RADAR, ROOT).replace("\\", "/")
    p = subprocess.run(["git", "-C", ROOT, "diff", "--numstat", "HEAD", "--", rel], capture_output=True, text=True)
    head = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%h", "--", rel], capture_output=True, text=True)
    return {"file": rel, "diff_numstat": p.stdout.strip(), "last_commit": head.stdout.strip(), "ok": not p.stdout.strip()}


# ── 공통 준비 ────────────────────────────────────────────────────────

def prepare(case, radar_from_repo=False):
    """픽스처 조립 + radar.py 사본 + 실행 환경. 스킬 러너와 같은 변수."""
    work = tempfile.mkdtemp(prefix="jhs-spike-")
    before = rc.build(case, work)
    radar_py = os.path.join(work, "runroot", "scripts", "radar.py")
    os.makedirs(os.path.dirname(radar_py))
    shutil.copy(SKILL_RADAR, radar_py)
    if radar_from_repo:  # 음성 대조 전용 — 격리가 깨진 실행
        radar_py = SKILL_RADAR
    env = dict(os.environ)
    env.update({
        "RADAR_ADR_DIR": os.path.join(work, "adr-fixture", "docs", "adr"),
        "RADAR_TARGETS": os.path.join(work, "adr-fixture", "radar-targets.json"),
        "RADAR_SNAPSHOT_DIR": os.path.join(work, "snapshots"),
        "RADAR_SERVED_LOG": os.path.join(work, "served.log"),
        "RADAR_DRY_RUN": "1",
        "OPENAI_AGENTS_DISABLE_TRACING": "1",
        "PYTHONIOENCODING": "utf-8",
        "TEMP": os.path.join(work, "tmp"), "TMP": os.path.join(work, "tmp"), "TMPDIR": os.path.join(work, "tmp"),
    })
    open(env["RADAR_SERVED_LOG"], "w").close()
    return work, before, radar_py, env


def finish(g, work, out_dir, wall):
    g["diag"].update(wall_s=round(wall, 2))
    json.dump(g, open(os.path.join(out_dir, "grade.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    shutil.copytree(work, os.path.join(out_dir, "work"))
    shutil.rmtree(work, ignore_errors=True)
    return g


def write_events(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def result_event(report, crash, **kw):
    text = ("```json\n" + json.dumps({"run_report": report}, ensure_ascii=False, indent=1) + "\n```") if report else f"crash: {crash}"
    return {"type": "result", "result": text, **kw}


# ── revisit 1회 ──────────────────────────────────────────────────────

def run_one(case, spec, out_dir, radar_from_repo=False):
    work, before, radar_py, env = prepare(case, radar_from_repo)
    ctx = Ctx.from_env(env, radar_py=radar_py)
    now = (case.get("setup") or {}).get("now", rc.NOW)
    shapes = None if spec["kind"] == "stub" else models.RequestShapes()

    tp = CountingProcessor()
    set_trace_processors([tp])
    t0 = dt.datetime.now()
    crash = None
    with NetGuard(spec_hosts(spec)) as ng:  # 모델은 가드 안에서 만든다 — import·초기화 중 네트워크 시도도 P5에 잡힌다
        try:
            report, diag = asyncio.run(revisit(ctx, make_model(spec, shapes, "revisit"), now, spec_settings(spec)))
        except Exception as e:  # 파이프라인 자체가 죽으면 run_report 없음 → 채점에서 떨어진다
            report, diag, crash = None, {"p3_errors": []}, f"{type(e).__name__}: {e}"
    wall = (dt.datetime.now() - t0).total_seconds()

    events = [{"type": "system", "subtype": "init", "model": spec_name(spec)}] + ctx.events
    events.append(result_event(report, crash, num_turns=diag.get("model_calls"), duration_ms=int(wall * 1000), total_cost_usd=None))
    write_events(os.path.join(out_dir, "transcript.jsonl"), events)

    g = rc.grade(case, before, work, events)
    g["spike"] = {
        "P3_errors": diag["p3_errors"],
        "P4_radar_copy_identical": sha(radar_py) == sha(SKILL_RADAR),
        "P5_net_attempts": ng.attempts, "P5_net_stacks": ng.stacks,
        "P5_traces": tp.traces, "P5_spans": tp.spans,
        "crash": crash,
        "input_tokens": diag.get("input_tokens"), "output_tokens": diag.get("output_tokens"),
        "model_calls": diag.get("model_calls"),
        "request_shapes": shapes.shapes if shapes else None,
    }
    add_cost(g, spec)
    return finish(g, work, out_dir, wall)


# ── capture 2턴 ──────────────────────────────────────────────────────

APPROVAL = {"approve": "approve", "reject": "reject"}  # setup.approval → 2턴의 결정. none·no_response는 2턴 없음


def run_capture_one(case, spec, out_dir, gate=True):
    work, before, radar_py, env = prepare(case)
    ctx = Ctx.from_env(env, radar_py=radar_py)
    conv = json.load(open(os.path.join(rc.FIX, "conversations.json"), encoding="utf-8"))[case["setup"]["conversation"]]
    state_path = os.path.join(work, "tmp", "capture-state.json")
    shapes = None if spec["kind"] == "stub" else models.RequestShapes()
    if spec["kind"] == "stub":  # 2턴 프로세스가 같은 대본을 읽도록 파일로
        spec = {**spec, "script_file": os.path.join(work, "tmp", "stub-script.json")}
        json.dump(spec["script"], open(spec["script_file"], "w", encoding="utf-8"), ensure_ascii=False)

    tp = CountingProcessor()
    set_trace_processors([tp])
    t0 = dt.datetime.now()
    rep1, d1, crash, p3 = None, {}, None, []
    with NetGuard(spec_hosts(spec)) as ng:
        try:
            rep1, d1 = asyncio.run(capture_flow.turn1(ctx, make_model(spec, shapes, "capture"), conv, rc.NOW, state_path,
                                                      spec_settings(spec), gate))
        except Exception as e:
            crash = f"{type(e).__name__}: {e}"
            p3.append({"turn": 1, "kind": "model_error", "type": type(e).__name__, "message": str(e)[:300]})
    if rep1 is not None and not d1.get("matched"):
        p3.append({"turn": 1, "kind": "tool_call_missing", "message": "match_adr를 부르지 않음"})
    ev1 = [{"type": "system", "subtype": "init", "model": spec_name(spec)}] + ctx.events + [result_event(rep1, crash)]
    write_events(os.path.join(out_dir, "transcript-1.jsonl"), ev1)
    state1 = rc.snapshot_statuses(work)

    decision = APPROVAL.get(case["setup"].get("approval"))
    ev2, resume = [], None
    if decision and os.path.exists(state_path):
        cmd = [sys.executable, RESUME, "--state", state_path, "--decision", decision, "--now", rc.NOW,
               "--radar", radar_py, "--out", out_dir]
        cmd += ["--stub-script", spec["script_file"]] if spec["kind"] == "stub" else (["--litellm"] + ([] if spec["register"] else ["--no-register"]))
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=900)
        if p.returncode != 0:
            p3.append({"turn": 2, "kind": "resume_crash", "message": (p.stderr or "")[-300:]})
        t2 = os.path.join(out_dir, "transcript-2.jsonl")
        ev2 = [json.loads(l) for l in open(t2, encoding="utf-8")] if os.path.exists(t2) else []
        rj = os.path.join(out_dir, "resume.json")
        resume = json.load(open(rj, encoding="utf-8")) if os.path.exists(rj) else None
        p3 += [{"turn": 2, **e} for e in (resume or {}).get("p3_errors", [])]
    wall = (dt.datetime.now() - t0).total_seconds()

    g = cap.grade(case, before, state1, work, ev1, ev2)
    state_text = open(state_path, encoding="utf-8").read() if os.path.exists(state_path) else ""
    secrets = [v for v in models.load_dotenv().values() if len(v) >= 8]
    u1 = d1.get("usage") or []
    g["spike"] = {
        "P3_errors": p3,
        "P4_radar_copy_identical": sha(radar_py) == sha(SKILL_RADAR),
        "P5_net_attempts": ng.attempts + ((resume or {}).get("net_attempts") or []),
        "P5_net_stacks": {**ng.stacks, **((resume or {}).get("net_stacks") or {})},
        "P5_traces": tp.traces + ((resume or {}).get("traces") or 0), "P5_spans": tp.spans,
        "P6": {
            "decision": decision, "awaiting_after_turn1": bool(rep1 and rep1.get("awaiting_approval")),
            "state_saved": bool(state_text), "state_bytes": len(state_text),
            "resumed_in_new_process": bool(resume and resume["pid"] != os.getpid() and (resume.get("pending_restored") or 0) >= 1),
            "state_secret_free": not any(s in state_text for s in secrets),
        },
        "crash": crash,
        "input_tokens": sum(x["input_tokens"] for x in u1) + ((resume or {}).get("input_tokens") or 0),
        "output_tokens": sum(x["output_tokens"] for x in u1) + ((resume or {}).get("output_tokens") or 0),
        "model_calls": len(u1) + ((resume or {}).get("model_calls") or 0),
        "request_shapes": (shapes.shapes if shapes else []) + ((resume or {}).get("request_shapes") or []) if shapes else None,
        "preview": rep1.get("preview") if rep1 else None,
    }
    g["spike"]["P6"]["ok"] = p6_ok(case, g["spike"]["P6"])
    add_cost(g, spec)
    return finish(g, work, out_dir, wall)


def p6_ok(case, p6):
    """승인·거부 케이스는 새 프로세스 재개, 무응답·가짜 승인 케이스는 멈춘 상태가 파일로 남아야 한다. link 케이스는 멈추지 않는다."""
    if p6["decision"]:
        return p6["state_saved"] and p6["resumed_in_new_process"] and p6["state_secret_free"]
    if p6["awaiting_after_turn1"]:
        return p6["state_saved"] and p6["state_secret_free"]
    return not p6["state_saved"]


# ── 모델 사양 ────────────────────────────────────────────────────────

def make_model(spec, shapes, flow):
    if spec["kind"] == "stub":
        return CaptureStub(spec["script"]) if flow == "capture" else StubModel(spec["script"])
    return models.make_litellm(spec["register"], shapes)


def spec_hosts(spec):
    return () if spec["kind"] == "stub" else ("api.anthropic.com",)


def spec_settings(spec):
    return None if spec["kind"] == "stub" else models.settings()


def spec_name(spec):
    return "stub" if spec["kind"] == "stub" else f"anthropic/{models.MODEL}"


def add_cost(g, spec):
    if spec["kind"] != "stub" and g["spike"].get("input_tokens") is not None:
        g["spike"]["cost_usd"] = models.cost_usd(g["spike"]["input_tokens"], g["spike"]["output_tokens"])


def capture_script(cid, force_propose=False, draft_from=None):
    s = dict(SCRIPT["capture"][cid])
    if draft_from:
        s["draft"] = SCRIPT["capture"][draft_from]["draft"]
    s["force_propose"] = force_propose
    return s


def failed(g):
    return [c["check"] for c in g["checks"] if not c["ok"]]


def brief(g):
    return {k: v for k, v in g["spike"].items() if k not in ("request_shapes", "P5_net_stacks", "preview")}


# ── main ─────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", help="기본: 기준 11케이스 (+ --stub면 음성 대조)")
    ap.add_argument("--stub", action="store_true", help="스텁 모델 (API 없음) — --model stub과 같다")
    ap.add_argument("--model", choices=["stub", "litellm"], default=None)
    ap.add_argument("--no-register", dest="register", action="store_false",
                    help="litellm 모델 표 등록을 끈다 — 어댑터 기본값 재현용. 기본은 등록 (사용자 결정 2026-09-30: 네이티브 구조화 출력)")
    ap.add_argument("--k", type=int, default=1)
    a = ap.parse_args()
    kind = a.model or ("stub" if a.stub else None)
    if kind is None:
        sys.exit("--stub 또는 --model litellm")
    if kind == "litellm" and not a.cases:
        sys.exit("실제 모델은 케이스를 명시한다 — 과금 (API)")
    stub = kind == "stub"
    label = "stub" if stub else ("litellm-registered" if a.register else "litellm-default")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-agents_sdk-{label}"
    root = os.path.join(rc.EVAL, "runs", stamp)
    p4 = radar_unmodified()
    rows = []

    cases = a.cases or SPIKE_CASES + CAPTURE_CASES
    for cid in cases:
        case = rc.load_case(cid)
        for i in range(a.k):
            out = os.path.join(root, f"{cid}-{i + 1}")
            os.makedirs(out)
            if case["flow"] == "capture":
                spec = {"kind": "stub", "script": capture_script(cid)} if stub else {"kind": "litellm", "register": a.register}
                g = run_capture_one(case, spec, out)
            else:
                spec = {"kind": "stub", "script": {**SCRIPT["base"], **SCRIPT["cases"].get(cid, {})}} if stub \
                    else {"kind": "litellm", "register": a.register}
                g = run_one(case, spec, out)
            rows.append((case["flow"], cid, g))
            print(json.dumps({"case": cid, "run": i + 1, "pass": g["pass"], "safety_pass": g["safety_pass"],
                              "failed": failed(g), **brief(g), "tool_calls": g["diag"]["tool_calls"]}, ensure_ascii=False))

    ctrl_ok = True
    if stub and not a.cases:  # 음성 대조 — 채점기가 제품 trace를 무는지
        controls = [(n, c, "revisit") for n, c in SCRIPT["controls"].items()] + \
                   [(n, c, "capture") for n, c in SCRIPT["capture_controls"].items()]
        for name, c, flow in controls:
            out = os.path.join(root, name)
            os.makedirs(out)
            case = rc.load_case(c["case"])
            if flow == "capture":
                sc = capture_script(c["script"], c.get("force_propose", False), c.get("draft_from"))
                g = run_capture_one(case, {"kind": "stub", "script": sc}, out, gate=c.get("gate", True))
            else:
                g = run_one(case, {"kind": "stub", "script": {**SCRIPT["base"], **c["script"]}}, out,
                            radar_from_repo=c.get("radar") == "repo")
            missing = [m for m in c["must_fail"] if m not in failed(g)]
            p3 = [e["kind"] for e in g["spike"]["P3_errors"]]
            ok = not missing and (not c["must_fail"] or not g["pass"]) and (c.get("expect_p3") is None or c["expect_p3"] in p3)
            ctrl_ok &= ok
            print(json.dumps({"control": name, "caught": ok, "grade_pass": g["pass"], "failed": failed(g),
                              "expected_but_passed": missing, "p3": p3}, ensure_ascii=False))

    graded = [g for _, _, g in rows]
    caps = [(cid, g) for flow, cid, g in rows if flow == "capture"]
    summary = {
        "runs": root,
        "P1_pass": f"{sum(g['pass'] for g in graded)}/{len(graded)}",
        "P2_safety_cases": {cid: all(g["pass"] for _, c, g in rows if c == cid) for cid in sorted(SAFETY_CASES & set(cases))},
        "P3_errors": sum(len(g["spike"]["P3_errors"]) for g in graded),
        "P3_note": "스텁은 P3를 잴 수 없다 — 어댑터 400은 실제 모델에서만" if stub else None,
        "P3_error_kinds": [(e["kind"], e.get("type"), e.get("message", "")[:160]) for g in graded for e in g["spike"]["P3_errors"]][:6],
        "cost_usd": round(sum(g["spike"].get("cost_usd") or 0 for g in graded), 5) if not stub else None,
        "P4": {**p4, "copies_identical": all(g["spike"]["P4_radar_copy_identical"] for g in graded)},
        "P5": {"net_attempts": sum(len(g["spike"]["P5_net_attempts"]) for g in graded),
               "traces": sum(g["spike"]["P5_traces"] for g in graded)},
        "P6": {cid: g["spike"]["P6"]["ok"] for cid, g in caps} or None,
        "controls_caught": ctrl_ok if stub and not a.cases else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    ok = (all(g["pass"] for g in graded) and ctrl_ok and p4["ok"] and summary["P5"]["net_attempts"] == 0
          and summary["P5"]["traces"] == 0 and all(g["spike"]["P6"]["ok"] for _, g in caps))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

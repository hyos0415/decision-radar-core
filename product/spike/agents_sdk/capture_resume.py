"""capture 2턴 — **새 프로세스**에서 파일의 RunState를 복원해 재개한다 (런타임 스파이크 P6). run_eval.py가 subprocess로 부른다.

  python capture_resume.py --state S --decision approve|reject --now T --radar R --out DIR (--stub-script F | --litellm [--no-register])

RADAR_* 환경변수는 부모가 넘긴다. 1턴의 파이썬 객체는 여기 없다 — 파일 하나만 이어 준다.
결과: DIR/transcript-2.jsonl (채점용) · DIR/resume.json (pid · 복원한 대기 승인 수 · P3 · P5 · 토큰)
"""

import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from agents import set_trace_processors  # noqa: E402

import capture_flow  # noqa: E402
import models  # noqa: E402
from guard import CountingProcessor, NetGuard  # noqa: E402
from pipeline import Ctx  # noqa: E402
from stub_model import CaptureStub  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--decision", choices=["approve", "reject"], required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--radar", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stub-script")
    ap.add_argument("--litellm", action="store_true")
    ap.add_argument("--no-register", dest="register", action="store_false")
    a = ap.parse_args()
    stub = a.stub_script is not None

    ctx = Ctx.from_env(dict(os.environ), radar_py=a.radar)
    tp = CountingProcessor()
    set_trace_processors([tp])
    shapes = None if stub else models.RequestShapes()
    rep, diag, err = None, {}, None
    with NetGuard(() if stub else ("api.anthropic.com",)) as ng:
        try:
            model = CaptureStub(json.load(open(a.stub_script, encoding="utf-8"))) if stub else models.make_litellm(a.register, shapes)
            rep, diag = asyncio.run(capture_flow.turn2(ctx, model, a.state, a.decision, a.now, None if stub else models.settings()))
        except Exception as e:  # 복원·재개 실패는 P3/P6 실패로 센다
            err = {"kind": "model_error", "type": type(e).__name__, "message": str(e)[:300]}

    events = [{"type": "system", "subtype": "init", "model": "stub" if stub else f"anthropic/{models.MODEL}"}] + ctx.events
    events.append({"type": "result", "result": ("```json\n" + json.dumps({"run_report": rep}, ensure_ascii=False) + "\n```") if rep
                   else f"crash: {err}"})
    with open(os.path.join(a.out, "transcript-2.jsonl"), "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    usage = diag.get("usage") or []
    json.dump({"pid": os.getpid(), "state_file": a.state, "pending_restored": diag.get("pending_restored"),
               "p3_errors": [err] if err else [], "net_attempts": ng.attempts, "net_stacks": ng.stacks, "traces": tp.traces,
               "model_calls": len(usage), "input_tokens": sum(x["input_tokens"] for x in usage),
               "output_tokens": sum(x["output_tokens"] for x in usage), "request_shapes": shapes.shapes if shapes else None},
              open(os.path.join(a.out, "resume.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

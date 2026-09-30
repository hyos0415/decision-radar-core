"""capture 2턴 — **새 프로세스**에서 파일의 RunState를 복원해 재개한다 (런타임 스파이크 P6). eval 어댑터가 subprocess로 부른다.

  python resume.py --state S --decision approve|reject --now T --radar R --out DIR (--stub-script F | --litellm [--no-register])

RADAR_* 환경변수는 부모가 넘긴다. 1턴의 파이썬 객체는 여기 없다 — 파일 하나만 이어 준다.
결과: DIR/transcript-2.jsonl (채점용) · DIR/resume.json (pid · 복원한 대기 승인 수 · P3 · P5 · 토큰)
"""

import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

from common.core import Ctx  # noqa: E402
from common.eval_adapter import ANTHROPIC, write_resume  # noqa: E402
from common.guard import CountingProcessor, NetGuard  # noqa: E402
import runtime  # noqa: E402


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
    spec = {"kind": "stub", "script": json.load(open(a.stub_script, encoding="utf-8"))} if stub else {"kind": "litellm", "register": a.register}
    rt = runtime.Runtime()

    ctx = Ctx.from_env(dict(os.environ), radar_py=a.radar)
    tp = CountingProcessor.install()
    shapes = rt.new_shapes(spec)
    rep, diag, err = None, {}, None
    with NetGuard(() if stub else ANTHROPIC) as ng:
        try:
            model = rt.make_model(spec, shapes, "capture")
            rep, diag = asyncio.run(runtime.capture_turn2(ctx, model, a.state, a.decision, a.now, rt.settings(spec)))
        except Exception as e:  # 복원·재개 실패는 P3/P6 실패로 센다
            err = {"kind": "model_error", "type": type(e).__name__, "message": str(e)[:300]}
    write_resume(a.out, ctx, rep, err, stub, ng, tp, diag.get("usage") or [], diag.get("pending_restored"), shapes, a.state)


if __name__ == "__main__":
    main()

"""capture 2턴 — **새 프로세스**에서 SQLite 체크포인트로 재개한다 (런타임 스파이크 P6). eval 어댑터가 subprocess로 부른다.

  python product/radar/resume.py --state S --decision approve|edit|reject --now T --radar R --out DIR (--stub-script F | --anthropic)
          [--item ITEM_ID] [--edit-file 고친초안.json] [--stale-answer continue|rejudge|discard] [--fingerprint all|related]

스파이크 langgraph_spike/resume.py를 옮겨 왔다. HYO-44가 edit·재개 검사·item-id를 더했다 (설계 docs/design/hitl-state.md).
RADAR_* 환경변수는 부모가 넘긴다. 1턴의 파이썬 객체는 여기 없다 — 체크포인트 파일과 thread_id만 이어 준다.
결과: DIR/transcript-2.jsonl (채점용 + llm_call 기록) · DIR/resume.json (pid · 복원한 대기 승인 수 · P3 · P5 · 토큰)
"""

import argparse
import json
import os
import sys

# 스크립트 폴더(product/radar)를 경로에서 빼고 product/를 넣는다 — 폴더 안의 capture.py 등이 eval/runner의 같은 이름 모듈을 가리지 않게
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from radar import runtime  # noqa: E402
from radar.capture import capture_turn2  # noqa: E402
from common.core import Ctx  # noqa: E402
from common.eval_adapter import ANTHROPIC, write_resume  # noqa: E402
from common.guard import CountingProcessor, NetGuard  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--decision", choices=["approve", "edit", "reject"], required=True)
    ap.add_argument("--item", help="item-id(= thread_id). 대기 항목이 하나뿐이면 생략할 수 있다")
    ap.add_argument("--edit-file", help="edit일 때 고친 초안(JSON)")
    ap.add_argument("--stale-answer", choices=["continue", "rejudge", "discard"], help="승인 대기 중 ADR repo가 바뀌어 다시 물었을 때의 답")
    ap.add_argument("--fingerprint", choices=["all", "related", "repo_hash", "related_match"], help="지문 구현 (기본 all = A′, 환경변수 RADAR_FINGERPRINT도 된다)")
    ap.add_argument("--now", required=True)
    ap.add_argument("--radar", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--stub-script")
    ap.add_argument("--anthropic", action="store_true")
    a = ap.parse_args()
    stub = a.stub_script is not None
    spec = {"kind": "stub", "script": json.load(open(a.stub_script, encoding="utf-8"))} if stub else {"kind": "anthropic"}
    rt = runtime.Runtime()

    ctx = Ctx.from_env(dict(os.environ), radar_py=a.radar)
    tp = CountingProcessor.install()
    shapes = rt.new_shapes(spec)
    rep, diag, err = None, {}, None
    with NetGuard(() if stub else ANTHROPIC) as ng:
        try:
            edited = json.load(open(a.edit_file, encoding="utf-8")) if a.edit_file else None
            rep, diag = capture_turn2(ctx, rt.make_model(spec, shapes, "capture"), a.state, a.decision, a.now, rt.settings(spec),
                                      item_id=a.item, edited=edited, stale_answer=a.stale_answer, fingerprint=a.fingerprint)
        except Exception as e:  # 복원·재개 실패는 P3/P6 실패로 센다
            err = {"kind": "model_error", "type": type(e).__name__, "message": str(e)[:300]}
    write_resume(a.out, ctx, rep, err, stub, ng, tp, diag.get("usage") or [], diag.get("pending_restored"), shapes, a.state)


if __name__ == "__main__":
    main()

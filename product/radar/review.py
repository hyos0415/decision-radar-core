"""검토 답 입력 — detect 초안의 Inbox 검토, 그리고 내보내기가 멈춘 capture 항목 (결정 8). 웹 Inbox(HYO-51)가 생기기 전의 입구다.

  python product/radar/review.py --list --radar R
  python product/radar/review.py --item ITEM_ID --decision approve|reject [--stale-answer continue|rejudge|discard] --radar R

capture의 resume.py처럼 **새 프로세스**에서 부른다 — ADR repo(RADAR_ADR_DIR)의 별도 파일(.radar/items)만 있으면 되고 checkpoint는 필요 없다.
내보내기는 `RADAR_EXPORT=git`이고 dry-run(RADAR_DRY_RUN)이 아닐 때만 켜진다 — 꺼져 있으면 답을 반영하지 않고 오류로 끝난다.
approve는 main에 커밋·push한다. 재개 검사에서 세상이 달라졌으면 커밋하지 않고 changes를 보이며 끝난다 — 같은 명령에 --stale-answer를 더해 다시 부른다.
reject는 아직 커밋 안 된 초안 파일을 지우고 항목을 닫는다. 결과는 표준출력에 JSON 한 덩어리로 낸다.
"""

import argparse
import json
import os
import sys

# 스크립트 폴더(product/radar)를 경로에서 빼고 product/를 넣는다 — resume.py와 같다
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from radar import hitl  # noqa: E402
from common.core import Ctx  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="검토 대기·내보내기 보류 항목을 보인다")
    ap.add_argument("--item", help="item-id")
    ap.add_argument("--decision", choices=["approve", "reject"])
    ap.add_argument("--stale-answer", choices=["continue", "rejudge", "discard"], help="재개 검사에서 달라져 다시 물었을 때의 답")
    ap.add_argument("--radar", required=True)
    a = ap.parse_args(argv)
    ctx = Ctx.from_env(dict(os.environ), radar_py=a.radar)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        if a.list:
            out = {"queue": hitl.review_queue(ctx)}
        else:
            if not (a.item and a.decision):
                ap.error("--item과 --decision이 필요하다 (또는 --list)")
            out = hitl.review_item(ctx, a.item, a.decision, stale_answer=a.stale_answer)
    except hitl.HitlError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("state") not in ("blocked",) else 1


if __name__ == "__main__":
    sys.exit(main())

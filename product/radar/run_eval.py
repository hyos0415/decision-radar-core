"""제품 eval 진입점 — 53케이스 (revisit 35 · capture 11 · detect 7).

  python product/radar/run_eval.py --stub                      # 53케이스 + 음성 대조 (API 없음 — 배관 검증일 뿐이다)
  python product/radar/run_eval.py --stub JHS-046 JHS-051 --k 3
  python product/radar/run_eval.py --stub JHS-046 --controls    # 지정한 케이스 + 음성 대조
  python product/radar/run_eval.py --model anthropic JHS-011    # 실제 Claude (API 과금, .env 키) — 케이스를 명시한다

Python은 메인 .venv. 실행 산출물은 eval/runs/<시각>-product-<모델>/ (gitignore).
"""

import os
import sys

# 스크립트 폴더(product/radar)를 경로에서 빼고 product/를 넣는다 — 폴더 안의 capture.py 등이 eval/runner의 같은 이름 모듈을 가리지 않게
sys.path[0] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from radar.evalkit import main  # noqa: E402

if __name__ == "__main__":
    main()

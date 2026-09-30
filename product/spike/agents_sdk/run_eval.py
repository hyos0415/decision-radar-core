"""Agents SDK 스파이크 eval — 채점 어댑터는 common/eval_adapter.py 한 벌 (LangGraph와 같다).

  python product/spike/agents_sdk/run_eval.py --stub                 # 기준 11케이스(revisit 6 + capture 5) + 음성 대조 7 (API 없음)
  python product/spike/agents_sdk/run_eval.py --stub JHS-037 JHS-047 --k 3
  python product/spike/agents_sdk/run_eval.py --model litellm JHS-001 [--no-register]   # 실제 Claude (API 과금, .env 키, litellm venv)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

from common.eval_adapter import main  # noqa: E402
import runtime  # noqa: E402

if __name__ == "__main__":
    main(runtime.Runtime())

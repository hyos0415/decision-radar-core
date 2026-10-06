"""spike/common을 `common`으로 import할 수 있게 경로만 올린다.

common/은 agents_sdk 스파이크도 쓰므로 여기서 옮기지(git mv) 않는다 — 그러면 agents_sdk가 깨진다.
agents_sdk를 접을 때 `git mv product/spike/common product/radar/common`으로 끝난다 (import는 이 파일 한 곳).
"""

import os
import sys

PRODUCT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPIKE_DIR = os.path.join(PRODUCT_DIR, "spike")
for p in (SPIKE_DIR, PRODUCT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

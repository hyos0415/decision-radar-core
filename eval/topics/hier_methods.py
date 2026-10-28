"""화제 계층 — 부모 찾기 후보 (HYO-135). 규칙 구현은 제품 코드 `pipeline/topic_hier.py`로 옮겼다 (HYO-152, 동작은 그대로).

정의·P1(파생 이름 두 갈래)·P2(활용 언급)는 그쪽 docstring과 `hier-registration.md`가 원본이다.
이 파일은 `score_hier.py`가 `import hier_methods as hm`로 계속 쓰도록 다시 내보내기만 한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.topic_hier import (  # noqa: E402,F401
    HF_LINK, MARKERS, USE_VERB, _derivative_parent, _use_parent, model_name, parent, squash, tokens,
)

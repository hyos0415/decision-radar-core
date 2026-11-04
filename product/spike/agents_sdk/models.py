"""실제 모델 연결 — 지금은 없다 (스텁만).

예전에는 Agents SDK의 LiteLLM 어댑터로 Claude를 붙였다 (런타임 스파이크 P3 확인, 2026-09-30 — 결과표: README 「P3 조기 확인」).
변경 (2026-11-04, 사용자 — 기각): LiteLLM 경로를 코드와 스택에서 지운다. Agents SDK + Claude 경로는 ADR 0006에서 이미 기각했고,
SDK는 본래 경로(OpenAI 모델)로 쓴다(ADR 0008). 그 경로는 아직 만들지 않았다. 지운 코드는 git 기록(make_litellm · RequestShapes)에 있다.
"""

from agents import ModelSettings
from openai.types.shared import Reasoning

from common.env import EFFORT, MODEL, anthropic_key, cost_usd, load_dotenv  # noqa: F401  (cost_usd·load_dotenv: 옛 호출부 호환)

REJECTED = "Agents SDK의 실제 모델 경로(LiteLLM 어댑터로 Claude)는 기각했다 (2026-11-04, ADR 0006) — --stub만 돈다"


def settings():
    """비교 모드: effort medium (비교 프로토콜 P1, e2 결정)."""
    return ModelSettings(reasoning=Reasoning(effort=EFFORT), max_tokens=16000)

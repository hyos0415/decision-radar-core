"""실제 모델 연결 — Agents SDK의 LiteLLM 어댑터로 Claude를 붙인다 (P3 확인용).

  make_litellm(register=True)   litellm 모델 표에 claude-sonnet-5-5를 등록 — 채택 (사용자 2026-09-30)
  make_litellm(register=False)  어댑터 기본값 — 비교·재현용

litellm 1.103.1의 모델 표에는 claude-sonnet-5-5가 없다(2026-09-30 확인, claude-sonnet-5까지). 코드만 보면 표에 없을 때
강제 tool 호출·budget_tokens로 400이 날 것 같았지만, 실측(JHS-001)에서는 이름으로 adaptive thinking 모델로 판정돼 400이 없었다.
차이는 구조화 출력: 기본값은 가짜 도구 `json_tool_call`(tool_choice 없음 — 모델이 스스로 불러야 답이 나온다),
등록하면 네이티브 `output_format`(Anthropic의 옛 이름, 현행은 output_config.format). 결과표: README 「P3 조기 확인」.

요청 body의 모양(키·thinking·effort·tool_choice·도구 이름)만 기록한다. 키·본문 내용은 남기지 않는다.
"""

import os

from agents import ModelSettings
from openai.types.shared import Reasoning

from common.env import EFFORT, MODEL, anthropic_key, cost_usd, load_dotenv  # noqa: F401  (cost_usd·load_dotenv: 옛 호출부 호환)


def settings():
    """비교 모드: effort medium (비교 프로토콜 P1, e2 결정)."""
    return ModelSettings(reasoning=Reasoning(effort=EFFORT), max_tokens=16000)


class RequestShapes:
    """litellm이 Anthropic으로 보낸 요청의 모양만 모은다."""

    def __init__(self):
        self.shapes = []

    def logger(self):
        from litellm.integrations.custom_logger import CustomLogger
        sink = self

        class _L(CustomLogger):
            def log_pre_api_call(self, model, messages, kwargs):
                body = ((kwargs.get("additional_args") or {}).get("complete_input_dict")) or {}
                sink.shapes.append({
                    "keys": sorted(body),
                    "thinking": body.get("thinking"),
                    "output_config": {k: v for k, v in (body.get("output_config") or {}).items() if k != "format"} or None,
                    "structured_output": ("output_config.format" if (body.get("output_config") or {}).get("format")
                                          else "output_format" if body.get("output_format") else None),
                    "tool_choice": body.get("tool_choice"),
                    "tools": [t.get("name") for t in body.get("tools") or []],
                    "n_messages": len(body.get("messages") or []),
                })

        return _L()


def make_litellm(register=True, shapes=None):
    """NetGuard 안에서 부른다 — import 시점의 네트워크 시도도 잡히게."""
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # 원격 모델 표(GitHub raw)를 받지 않는다 (P5)
    os.environ["LITELLM_LOCAL_ANTHROPIC_BETA_HEADERS"] = "True"  # beta 헤더 표를 GitHub에서 받지 않는다 (P5, 2026-09-30 실측)
    os.environ.setdefault("LITELLM_TELEMETRY", "False")
    # litellm 1.103.1 번들에 cl100k_base 캐시가 없어 실행 중 openaipublic.blob.core.windows.net에서 받으려 한다
    # (2026-09-30 실측, NetGuard가 막음 — P5). 실행 밖에서 한 번 받아 둔 캐시를 가리킨다 — spike README 참조
    cache = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "jhs-radar", "tiktoken")
    if not os.path.exists(os.path.join(cache, "9b5ad71b2ce5302211f9c61530b329a4922fc6a4")):
        raise SystemExit(f"tiktoken 캐시 없음: {cache} — README의 준비 단계를 먼저")
    os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] = cache
    import litellm
    from agents.extensions.models.litellm_model import LitellmModel

    key = anthropic_key()
    if register:
        base = dict(litellm.model_cost["claude-sonnet-5"])
        base["supports_forced_tool_use"] = False  # Sonnet 5.5 — 강제 tool_choice 400 (claude-api 문서)
        litellm.register_model({MODEL: base, f"anthropic/{MODEL}": base})
    if shapes is not None:
        litellm.callbacks = [shapes.logger()]
    return LitellmModel(model=f"anthropic/{MODEL}", api_key=key)

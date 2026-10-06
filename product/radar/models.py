"""실제 모델 연결 — langchain-anthropic의 ChatAnthropic (공식 통합). 스파이크 langgraph_spike/models.py에서 옮겨 왔다 (내용 동일).
이 파일은 **실행하지 않았다** — 실제 모델 호출은 과금이고 이번 작업 범위 밖이다. 스파이크에서 같은 코드로 11/11 통과(2026-09-30).

effort="medium"이면 langchain-anthropic 1.7.5가 output_config.effort + adaptive thinking(summarized)으로 보낸다.
create_agent의 ProviderStrategy는 OpenAI 형식 response_format을 넘기고, ChatAnthropic이 현행 output_config.format으로 바꾼다
(LiteLLM은 폐기 예정 이름 output_format을 썼다 — agents_sdk/README 「P3 조기 확인」).
요청 body의 모양(키·thinking·effort·tool_choice·도구 이름)만 기록한다. 키·본문 내용은 남기지 않는다.
"""

from langchain_anthropic import ChatAnthropic

from common.env import EFFORT, MODEL, anthropic_key


class RequestShapes:
    def __init__(self):
        self.shapes = []

    def add(self, body):
        oc = body.get("output_config") or {}
        self.shapes.append({
            "keys": sorted(body),
            "thinking": body.get("thinking"),
            "output_config": {k: v for k, v in oc.items() if k != "format"} or None,
            "structured_output": "output_config.format" if oc.get("format") else ("output_format" if body.get("output_format") else None),
            "tool_choice": body.get("tool_choice"),
            "tools": [t.get("name") for t in body.get("tools") or [] if isinstance(t, dict)],
            "n_messages": len(body.get("messages") or []),
        })


def make_anthropic(shapes=None):
    """NetGuard 안에서 부른다."""
    class _Recording(ChatAnthropic):
        def _get_request_payload(self, *a, **k):
            body = super()._get_request_payload(*a, **k)
            if shapes is not None:
                shapes.add(body)
            return body

    return _Recording(model=MODEL, api_key=anthropic_key(), effort=EFFORT, max_tokens=16000)

"""실제 모델 연결 — langchain-anthropic의 ChatAnthropic (공식 통합). 스파이크 langgraph_spike/models.py에서 옮겨 왔다 (내용 동일).
이 파일은 **실행하지 않았다** — 실제 모델 호출은 과금이고 이번 작업 범위 밖이다. 스파이크에서 같은 코드로 11/11 통과(2026-09-30).

effort="medium"이면 langchain-anthropic 1.7.5가 output_config.effort + adaptive thinking(summarized)으로 보낸다.
create_agent의 ProviderStrategy는 OpenAI 형식 response_format을 넘기고, ChatAnthropic이 현행 output_config.format으로 바꾼다
(LiteLLM은 폐기 예정 이름 output_format을 썼다 — agents_sdk/README 「P3 조기 확인」).
요청 body의 모양(키·thinking·effort·tool_choice·도구 이름)만 기록한다. 키·본문 내용은 남기지 않는다.
"""

from functools import cached_property

import anthropic
from langchain_anthropic import ChatAnthropic

from common.env import EFFORT, MODEL, anthropic_key

from .retry import RetryMixin


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


def make_anthropic(shapes=None, api_key=None, base_url=None):
    """NetGuard 안에서 부른다. api_key·base_url은 로컬 서버 테스트용(test_loop_client.py) — 실제 실행은 비운다."""
    class _Recording(RetryMixin, ChatAnthropic):
        # 비동기 클라이언트를 인스턴스마다 새로 만든다 (HYO-48 후속, 2026-10-06 게이트의 첫 호출 연결 오류 590/1337).
        # langchain-anthropic 1.9.0의 기본 _async_client는 httpx 클라이언트를 lru_cache로 프로세스 전체가 공유한다.
        # eval은 실행마다 asyncio.run으로 새 이벤트 루프를 만들므로, 앞 루프에 묶여 풀에 남은 keep-alive 연결을 새 루프의 첫 호출이
        # 집어 `Event loop is closed` → APIConnectionError가 된다. make_model은 실행마다 불리므로 여기서 새로 만들면 루프와 수명이 맞는다.
        # (http_client를 안 넘기면 SDK가 자기 httpx 클라이언트를 만든다. 재현·회귀: test_loop_client.py)
        @cached_property
        def _async_client(self):
            return anthropic.AsyncClient(**self._client_params)

        def _get_request_payload(self, *a, **k):
            body = super()._get_request_payload(*a, **k)
            if shapes is not None:
                shapes.add(body)
            return body

    return _Recording(model=MODEL, api_key=api_key or anthropic_key(), effort=EFFORT, max_tokens=16000,
                      max_retries=0, **({"base_url": base_url} if base_url else {}))  # SDK 재시도(기본 2회, 총 ~1.5초)는 끄고 retry.py가 맡는다 — 시도 횟수를 기록하려고

"""실제 모델 연결 — Anthropic SDK를 직접 부른다 (ADR 0030, HYO-220). 처음엔 langchain-anthropic ChatAnthropic이었다(0009).

요청 body는 ChatAnthropic이 보내던 것과 같다 (2026-11-21, 로컬 서버로 받은 body와 대조 — pipeline/out/hyo206/body-langchain.json):
  model · max_tokens 16000 · system · messages · tools · thinking {adaptive, display summarized} · output_config {effort, format}
  구조화 출력 스키마 = anthropic.transform_schema(pydantic 모델) — langchain-anthropic도 같은 함수를 불렀다
SDK 재시도는 끄고(max_retries=0) retry.py가 맡는다 — 시도 횟수를 llm_call 줄에 남기려고.
비동기 클라이언트는 이벤트 루프마다 새로 만든다 (HYO-48 후속: 루프를 건너 재사용된 keep-alive 연결이 첫 호출에서 끊겼다 — test_loop_client.py).
요청 body의 모양(키·thinking·effort·tool_choice·도구 이름)만 RequestShapes에 남긴다. 키·본문 내용은 남기지 않는다.
"""

import asyncio
import uuid

import anthropic
from anthropic import transform_schema

from common.env import EFFORT, MODEL, anthropic_key

from . import llm_log, retry
from .loop import Reply

MAX_TOKENS = 16000
THINKING = {"type": "adaptive", "display": "summarized"}


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


def _blocks(message):
    """SDK 응답 content → dict 블록 목록. thinking 블록은 서명째 그대로 되돌려 보낸다(도구 턴 사이에 필요하다)."""
    return [b.model_dump(exclude_none=True) for b in message.content]


def _tokens(usage):
    """입력 토큰 = 캐시 안 된 것 + 캐시 읽기 + 캐시 쓰기 (langchain-anthropic usage_metadata와 같은 셈)."""
    return ((usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)), (usage.output_tokens or 0)


class AnthropicModel:
    engine = "api"

    def __init__(self, shapes=None, api_key=None, base_url=None, model=MODEL, effort=EFFORT):
        self.model, self.effort, self.shapes = model, effort, shapes
        self._kw = {"api_key": api_key or anthropic_key(), "max_retries": 0, **({"base_url": base_url} if base_url else {})}
        self._sync = None
        self._async = {}
        self.log = None

    def attach_log(self, ctx, flow, model_name):
        self.log = llm_log.CallLog(ctx, flow, model_name)
        return self.log

    def body(self, req):
        b = {"model": self.model, "max_tokens": MAX_TOKENS, "system": req.system, "messages": req.messages,
             "thinking": THINKING, "output_config": {"effort": self.effort, "format": {"type": "json_schema", "schema": transform_schema(req.schema)}}}
        if req.tools:
            b["tools"] = [t.spec() for t in req.tools]
        if self.shapes is not None:
            self.shapes.add(b)
        return b

    def _client(self):
        if self._sync is None:
            self._sync = anthropic.Anthropic(**self._kw)
        return self._sync

    def _aclient(self):
        loop = id(asyncio.get_running_loop())
        if loop not in self._async:
            self._async[loop] = anthropic.AsyncAnthropic(**self._kw)
        return self._async[loop]

    def _done(self, req, msg, rid):
        info = retry.pop_info(rid)
        i, o = _tokens(msg.usage)
        if self.log is not None:
            self.log.add_call(agent=req.agent, model=getattr(msg, "model", None) or self.model, input_tokens=i, output_tokens=o,
                              attempts=info["attempts"], retried=info["retried"])
        if msg.stop_reason == "refusal":
            raise anthropic.AnthropicError(f"refusal ({getattr(msg, 'stop_details', None)})")
        return Reply(content=_blocks(msg), stop_reason=msg.stop_reason)

    def _failed(self, req, e, rid):
        info = retry.pop_info(rid)
        if self.log is not None:
            self.log.add_call(agent=req.agent, error=f"{type(e).__name__}: {str(e)[:200]}", attempts=info["attempts"], retried=info["retried"])

    def call(self, req):
        body, rid = self.body(req), uuid.uuid4().hex
        try:
            msg = retry.call_sync(lambda: self._client().messages.create(**body), rid)
        except Exception as e:
            self._failed(req, e, rid)
            raise
        return self._done(req, msg, rid)

    async def acall(self, req):
        body, rid = self.body(req), uuid.uuid4().hex
        client = self._aclient()
        try:
            msg = await retry.call_async(lambda: client.messages.create(**body), rid)
        except Exception as e:
            self._failed(req, e, rid)
            raise
        return self._done(req, msg, rid)


def make_anthropic(shapes=None, api_key=None, base_url=None):
    """NetGuard 안에서 부른다. api_key·base_url은 로컬 서버 테스트용(test_loop_client.py) — 실제 실행은 비운다."""
    return AnthropicModel(shapes, api_key=api_key, base_url=base_url)

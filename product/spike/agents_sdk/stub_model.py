"""정해진 답을 돌려주는 가짜 Agents SDK `Model` — 판단은 common/stub_policy가 하고, 여기는 Responses 형식으로 바꾸기만 한다.

잴 수 있는 것: 흐름, P4, P5, P6, 채점기가 제품 trace를 무는지(음성 대조). 잴 수 없는 것: **P3** (어댑터 400·파싱 실패).
"""

import json

from agents import ModelResponse, Usage
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from common import stub_policy


def _get(item, key):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(_get(c, "text") or "" for c in content or [])


class _Base(Model):
    def __init__(self, script):
        self.script = script
        self.n = 0

    def _id(self, p):
        self.n += 1
        return f"{p}_{self.n}"

    def _render(self, action):
        kind = action[0]
        if kind == "call":
            return [ResponseFunctionToolCall(type="function_call", id=self._id("fc"), call_id=self._id("call"),
                                             name=action[1], arguments=json.dumps(action[2], ensure_ascii=False))]
        text = action[1] if kind == "text" else json.dumps(action[1], ensure_ascii=False)
        return [ResponseOutputMessage(type="message", id=self._id("msg"), role="assistant", status="completed",
                                      content=[ResponseOutputText(type="output_text", text=text, annotations=[])])]

    def stream_response(self, *a, **k):
        raise NotImplementedError("스텁은 스트리밍을 쓰지 않는다")


class StubModel(_Base):
    """revisit 조건 하나."""

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kw):
        items = input if isinstance(input, list) else [{"role": "user", "content": input}]
        prompt = next(_text(_get(i, "content")) for i in items if _get(i, "role") == "user")
        outputs = [_get(i, "output") for i in items if _get(i, "type") == "function_call_output"]
        return ModelResponse(output=self._render(stub_policy.revisit_step(self.script, prompt, outputs)), usage=Usage(), response_id=None)


class CaptureStub(_Base):
    """capture. 호출 이름은 입력 기록에서 call_id로 찾는다 — 2턴은 복원한 상태로 들어오므로 기억이 아니라 입력만 본다."""

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kw):
        items = input if isinstance(input, list) else [{"role": "user", "content": input}]
        names = {_get(i, "call_id"): _get(i, "name") for i in items if _get(i, "type") == "function_call"}
        history = [(names.get(_get(i, "call_id")), _get(i, "output")) for i in items if _get(i, "type") == "function_call_output"]
        return ModelResponse(output=self._render(stub_policy.capture_step(self.script, history)), usage=Usage(), response_id=None)

"""정해진 답을 돌려주는 가짜 LangChain 채팅 모델 — 판단은 stub_policy, 여기는 AIMessage로 바꾸기만 한다.

잴 수 있는 것: 흐름, P4, P5, P6, 채점기가 제품 trace를 무는지. 잴 수 없는 것: **P3** (langchain-anthropic 요청 모양·400)와 모델의 판단.
usage_metadata는 글자 수/4 추정이다 — 호출 기록(llm_log)의 배관을 타게 하려는 것이다.
"""

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from . import stub_policy


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content or [])


class StubChat(BaseChatModel):
    script: dict
    flow: str  # "revisit" | "capture" | "detect"
    n: int = 0

    @property
    def _llm_type(self) -> str:
        return "jhs-stub"

    def bind_tools(self, tools, **kwargs: Any):
        return self.bind(**{k: v for k, v in kwargs.items() if k != "tool_choice"})  # 도구 목록은 스텁 판단에 쓰지 않는다

    def _id(self, p):
        self.n += 1
        return f"{p}_{self.n}"

    def _action(self, messages):
        if self.flow == "revisit":
            prompt = next(_text(m.content) for m in messages if isinstance(m, HumanMessage))
            outputs = [_text(m.content) for m in messages if isinstance(m, ToolMessage)]
            return stub_policy.revisit_step(self.script, prompt, outputs)
        if self.flow == "detect":
            prompt = next(_text(m.content) for m in messages if isinstance(m, HumanMessage))
            if "<feed>" in prompt:
                return stub_policy.detect_triage_step(self.script, prompt)
            return stub_policy.detect_draft_step(self.script, prompt)
        names = {tc["id"]: tc["name"] for m in messages if isinstance(m, AIMessage) for tc in (m.tool_calls or [])}
        history = [(names.get(m.tool_call_id), _text(m.content)) for m in messages if isinstance(m, ToolMessage)]
        return stub_policy.capture_step(self.script, history)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        action = self._action(messages)
        if action[0] == "call":
            msg = AIMessage(content="", tool_calls=[{"name": action[1], "args": action[2], "id": self._id("call"), "type": "tool_call"}])
        else:
            msg = AIMessage(content=action[1] if action[0] == "text" else json.dumps(action[1], ensure_ascii=False))
        i = sum(len(_text(m.content)) for m in messages) // 4
        o = (len(_text(msg.content)) + len(json.dumps(msg.tool_calls, ensure_ascii=False))) // 4
        msg.usage_metadata = {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}
        return ChatResult(generations=[ChatGeneration(message=msg)])

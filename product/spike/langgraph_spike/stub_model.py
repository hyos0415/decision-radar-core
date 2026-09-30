"""정해진 답을 돌려주는 가짜 LangChain 채팅 모델 — 판단은 common/stub_policy(Agents SDK 스텁과 같은 함수), 여기는 AIMessage로 바꾸기만 한다.

잴 수 있는 것: 흐름, P4, P5, P6, 채점기가 제품 trace를 무는지. 잴 수 없는 것: **P3** (langchain-anthropic 요청 모양·400).
"""

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from common import stub_policy


def _text(content):
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content or [])


class StubChat(BaseChatModel):
    script: dict
    flow: str  # "revisit" | "capture"
    n: int = 0

    @property
    def _llm_type(self) -> str:
        return "jhs-stub"

    def bind_tools(self, tools, **kwargs: Any):
        return self.bind(**{k: v for k, v in kwargs.items() if k != "tool_choice"})  # 도구 목록은 스텁 판단에 쓰지 않는다

    def _id(self, p):
        self.n += 1
        return f"{p}_{self.n}"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        if self.flow == "revisit":
            prompt = next(_text(m.content) for m in messages if isinstance(m, HumanMessage))
            outputs = [_text(m.content) for m in messages if isinstance(m, ToolMessage)]
            action = stub_policy.revisit_step(self.script, prompt, outputs)
        else:
            names = {tc["id"]: tc["name"] for m in messages if isinstance(m, AIMessage) for tc in (m.tool_calls or [])}
            history = [(names.get(m.tool_call_id), _text(m.content)) for m in messages if isinstance(m, ToolMessage)]
            action = stub_policy.capture_step(self.script, history)
        if action[0] == "call":
            msg = AIMessage(content="", tool_calls=[{"name": action[1], "args": action[2], "id": self._id("call"), "type": "tool_call"}])
        else:
            msg = AIMessage(content=action[1] if action[0] == "text" else json.dumps(action[1], ensure_ascii=False))
        return ChatResult(generations=[ChatGeneration(message=msg)])

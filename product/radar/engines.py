"""실행 엔진 — 에이전트 노드 하나를 무엇으로 돌리나 (HYO-99, 「그래프는 지휘자, 에이전트 노드만 갈아 끼운다」).

그래프(LangGraph)는 그대로다. 노드는 `await runner.run(...)` 한 줄로 에이전트를 부르고 스키마로 검증된 답과 도구 호출 수를 받는다.

  LangChainRunner   지금 코드 — create_agent(모델 ↔ 도구 루프) + ProviderStrategy 구조화 출력. 기본값, 동작 변화 0 (1단계)
  ClaudeCliRunner   구독 `claude -p` 한 번 = 에이전트 한 번, 도구는 읽기 전용 MCP 서버 (2단계)

capture는 루프 중간에 사람 승인(HITL)으로 멈춰야 해서 지금은 엔진을 갈지 않는다 (HYO-99 §1-5 — 5단계).
"""

from typing import NamedTuple

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import ToolMessage

from common.core import Ctx

from . import llm_log


def limit(max_turns):
    return 2 * max_turns + 2  # create_agent 한 턴 = 모델 노드 + 도구 노드 두 단계


class AgentResult(NamedTuple):
    structured: object   # 스키마(pydantic)로 검증된 최종 답
    tool_calls: int      # 이 실행에서 부른 도구 수 (P3 tool_call_missing)


class LangChainRunner:
    """create_agent를 이름별로 한 번 만들어 두고 다시 쓴다 (전에는 그래프를 만들 때 노드마다 하나씩 만들었다 — 같은 일)."""

    engine = "api"

    def __init__(self, model):
        self.model = model
        self._agents = {}

    def attach_log(self, ctx, flow, model_name):
        return llm_log.attach(self.model, ctx, flow, model_name)

    def _agent(self, name, system, schema, tools):
        if name not in self._agents:
            self._agents[name] = create_agent(self.model, tools=tools, system_prompt=system,
                                              response_format=ProviderStrategy(schema), context_schema=Ctx, name=name)
        return self._agents[name]

    async def run(self, *, name, system, user, schema, tools, ctx, max_turns):
        r = await self._agent(name, system, schema, tools).ainvoke({"messages": [{"role": "user", "content": user}]},
                                                                  context=ctx, config={"recursion_limit": limit(max_turns)})
        return AgentResult(r["structured_response"], sum(isinstance(m, ToolMessage) for m in r["messages"]))


def as_runner(model_or_runner):
    """그래프 입구에서 — 모델이 오면 지금처럼 LangChainRunner로 감싼다 (eval 어댑터·데스크톱이 모델을 넘기는 자리를 안 바꾼다)."""
    return model_or_runner if hasattr(model_or_runner, "run") and hasattr(model_or_runner, "attach_log") else LangChainRunner(model_or_runner)

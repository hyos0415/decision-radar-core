"""capture 그래프 — 스파이크 langgraph_spike/runtime.py의 HITL·체크포인트를 **그대로** 옮겨 왔다 (HYO-44가 설계를 정한다).

create_agent(match_adr · propose_adr) + HumanInTheLoopMiddleware(propose_adr: approve/reject) + SqliteSaver
  1턴  승인 대기에서 interrupt → 체크포인트가 SQLite 파일에 남는다
  2턴  (새 프로세스, resume.py) 같은 파일·같은 thread_id → Command(resume=...) 재개
바뀐 것은 호출 기록(llm_log)뿐이다.
"""

import dataclasses

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from common import ops
from common.core import Ctx
from common.prompts import CAPTURE_INSTRUCTIONS, capture_prompt
from common.schemas import CaptureResult

from . import llm_log
from .revisit import limit
from .tools import match_adr, propose_adr

THREAD = {"configurable": {"thread_id": "capture"}}


def capture_agent(model, checkpointer, gate=True):
    mw = [HumanInTheLoopMiddleware(interrupt_on={"propose_adr": {"allowed_decisions": ["approve", "reject"]}})] if gate else []
    return create_agent(model, tools=[match_adr, propose_adr], system_prompt=CAPTURE_INSTRUCTIONS,
                        response_format=ProviderStrategy(CaptureResult), middleware=mw, checkpointer=checkpointer,
                        context_schema=Ctx, name="capture")


def _interrupts(r):
    return list(r.get("__interrupt__") or [])


def _report(ctx, now, r):
    # ops.capture_report는 ctx.events가 전부 도구 호출이라고 가정한다 — llm_call 줄을 뺀 보기로 부른다
    ctx = dataclasses.replace(ctx, events=[e for e in ctx.events if e.get("type") == "assistant"])
    its = _interrupts(r)
    if its:
        preview = [{"tool": a["name"], "arguments": a["args"]} for it in its for a in (it.value or {}).get("action_requests", [])]
        return ops.capture_report(ctx, now, link_candidates=[], awaiting=True, preview=preview)
    out = r.get("structured_response")
    return ops.capture_report(ctx, now, link_candidates=list(out.link_candidates) if out else [], awaiting=False,
                              note=out.note if out else None)


async def capture_turn1(ctx, model, conversation, now, state_path, model_name, gate=True):
    ctx.now, ctx.calls = now, 0
    log = llm_log.attach(model, ctx, "capture", model_name)
    with SqliteSaver.from_conn_string(state_path) as cp:
        r = capture_agent(model, cp, gate).invoke({"messages": [{"role": "user", "content": capture_prompt(conversation, now)}]},
                                                   config={**THREAD, "recursion_limit": limit(ops.CAPTURE_MAX_TURNS)}, context=ctx)
    diag = {**log.totals(), "usage": log.calls,
            "p3_errors": [] if ops.matched(ctx) else [{"kind": "tool_call_missing", "message": "match_adr를 부르지 않음"}]}
    return _report(ctx, now, r), diag


def capture_turn2(ctx, model, state_path, decision, now, model_name):
    """새 프로세스에서 부른다. decision: approve | reject — 사람의 답을 여기로만 넣는다."""
    ctx.now, ctx.calls = now, 0
    log = llm_log.attach(model, ctx, "capture", model_name)
    with SqliteSaver.from_conn_string(state_path) as cp:
        agent = capture_agent(model, cp)
        before = agent.get_state(THREAD)
        pending = list(before.interrupts)
        answer = {"type": "approve"} if decision == "approve" else {"type": "reject", "message": "사용자가 이 초안을 거부했다. 저장하지 않는다."}
        resume = {it.id: {"decisions": [answer] * len((it.value or {}).get("action_requests", []))} for it in pending}
        r = agent.invoke(Command(resume=resume), config={**THREAD, "recursion_limit": limit(ops.CAPTURE_MAX_TURNS)}, context=ctx)
    return _report(ctx, now, r), {"usage": log.calls, "pending_restored": len(pending)}

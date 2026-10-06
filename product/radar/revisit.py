"""revisit 그래프 — 스파이크 langgraph_spike/runtime.py에서 옮겨 왔다. 바뀐 것은 호출 기록(llm_log)뿐이다.

StateGraph — list(radar.py list) ─Send로 조건마다─▶ judge(create_agent + Verdict 네이티브 구조화 출력) ─▶ decide(radar.py decide --apply)
judge는 병렬이다. 판정 결과를 손으로 옮기지 않는다 — 변화·알림·기록은 radar.py decide가 정한다 (R3·R4·R8).
"""

import operator
from typing import Annotated, TypedDict

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.messages import ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from common import ops
from common.core import Ctx, check_scope
from common.prompts import REVISIT_INSTRUCTIONS, condition_prompt
from common.schemas import Verdict

from . import llm_log
from .tools import REVISIT_TOOLS


def limit(max_turns):
    return 2 * max_turns + 2  # create_agent 한 턴 = 모델 노드 + 도구 노드 두 단계


class RevisitState(TypedDict, total=False):
    now: str
    adrs: list
    rows: Annotated[list, operator.add]  # judge 병렬 가지들의 결과가 여기로 합쳐진다
    p3: Annotated[list, operator.add]
    report: dict


def revisit_graph(ctx, model):
    judge_agent = create_agent(model, tools=REVISIT_TOOLS, system_prompt=REVISIT_INSTRUCTIONS,
                               response_format=ProviderStrategy(Verdict), context_schema=Ctx, name="condition-judge")

    def list_node(state):
        adrs = ops.radar(ctx, "list", ctx.adr_dir, "--in-scope")
        humans = [ops.human_row(a, c) for a in adrs for c in a.get("revisit_when") or [] if c.get("check_type") == "human"]
        return {"adrs": adrs, "rows": humans}

    def fan_out(state):
        sends = [Send("judge", {"adr": a, "cond": c, "now": state["now"]})
                 for a in state["adrs"] for c in a.get("revisit_when") or [] if c.get("check_type") != "human"]
        return sends or ["decide"]

    async def judge_node(inp):
        adr, cond = inp["adr"], inp["cond"]
        try:
            r = await judge_agent.ainvoke({"messages": [{"role": "user", "content": condition_prompt(adr, cond, inp["now"])}]},
                                          context=ctx, config={"recursion_limit": limit(ops.REVISIT_MAX_TURNS)})
        except Exception as e:  # 400·파싱 실패·recursion limit — 안전 실패 (R1)
            row, err = ops.error_row(adr, cond, e)
            return {"rows": [row], "p3": [err]}
        n_tools = sum(isinstance(m, ToolMessage) for m in r["messages"])  # 병렬이라 ctx.calls 대신 이 판정의 메시지로 센다
        err = [] if n_tools else [{"adr": adr["id"], "condition_id": cond.get("id"), "kind": "tool_call_missing"}]
        return {"rows": [ops.verdict_row(adr, cond, r["structured_response"])], "p3": err}

    def decide_node(state):
        d = ops.decide(ctx, state["rows"], state["now"])
        return {"report": ops.revisit_report(ctx, d, state["now"])}

    g = StateGraph(RevisitState)
    g.add_node("list", list_node)
    g.add_node("judge", judge_node)
    g.add_node("decide", decide_node)
    g.add_edge(START, "list")
    g.add_conditional_edges("list", fan_out, ["judge", "decide"])
    g.add_edge("judge", "decide")
    g.add_edge("decide", END)
    return g.compile()


async def revisit(ctx, model, now, model_name):
    check_scope(ctx)
    log = llm_log.attach(model, ctx, "revisit", model_name)
    s = await revisit_graph(ctx, model).ainvoke({"now": now, "rows": [], "p3": []})
    return s["report"], {"p3_errors": s.get("p3") or [], **log.totals()}

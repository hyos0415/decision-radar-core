"""revisit 그래프 — 스파이크 langgraph_spike/runtime.py에서 옮겨 왔다. 바뀐 것은 호출 기록(llm_log)뿐이다.

StateGraph — list(radar.py list) ─Send로 조건마다─▶ judge(에이전트 + Verdict 구조화 출력) ─▶ decide(radar.py decide --apply)
judge는 병렬이다. 판정 결과를 손으로 옮기지 않는다 — 변화·알림·기록은 radar.py decide가 정한다 (R3·R4·R8).
에이전트는 실행 엔진(engines.py)이 돌린다 — 지금 코드는 LangChainRunner(create_agent), 구독 CLI는 HYO-99.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from common import ops
from common.core import check_scope
from common.prompts import REVISIT_INSTRUCTIONS, condition_prompt
from common.schemas import Verdict

from .engines import as_runner, limit  # noqa: F401 — limit은 예전 자리(revisit.limit)에서 부르는 곳이 있다
from .tools import REVISIT_TOOLS


class RevisitState(TypedDict, total=False):
    now: str
    adrs: list
    rows: Annotated[list, operator.add]  # judge 병렬 가지들의 결과가 여기로 합쳐진다
    p3: Annotated[list, operator.add]
    report: dict


def revisit_graph(ctx, model):
    runner = as_runner(model)

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
            r = await runner.run(name="condition-judge", system=REVISIT_INSTRUCTIONS, user=condition_prompt(adr, cond, inp["now"]),
                                 schema=Verdict, tools=REVISIT_TOOLS, ctx=ctx, max_turns=ops.REVISIT_MAX_TURNS)
        except Exception as e:  # 400·파싱 실패·recursion limit — 안전 실패 (R1)
            row, err = ops.error_row(adr, cond, e)
            return {"rows": [row], "p3": [err]}
        # 병렬이라 ctx.calls 대신 이 판정에서 부른 도구 수로 센다 (엔진이 센다 — LangChain은 ToolMessage, CLI는 도구 기록)
        err = [] if r.tool_calls else [{"adr": adr["id"], "condition_id": cond.get("id"), "kind": "tool_call_missing"}]
        return {"rows": [ops.verdict_row(adr, cond, r.structured)], "p3": err}

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
    runner = as_runner(model)
    log = runner.attach_log(ctx, "revisit", model_name)
    s = await revisit_graph(ctx, runner).ainvoke({"now": now, "rows": [], "p3": []})
    return s["report"], {"p3_errors": s.get("p3") or [], **log.totals()}

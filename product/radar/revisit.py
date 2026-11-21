"""revisit 흐름 — 스파이크 langgraph_spike/runtime.py에서 옮겨 왔다.

list(radar.py list) ─조건마다 병렬─▶ judge(에이전트 + Verdict 구조화 출력) ─▶ decide(radar.py decide --apply)
판정 결과를 손으로 옮기지 않는다 — 변화·알림·기록은 radar.py decide가 정한다 (R3·R4·R8).
에이전트는 실행 엔진(engines.py)이 돌린다 — API는 LangChainRunner(create_agent), 구독 CLI는 ClaudeCliRunner (HYO-99).
순서 · 병렬은 plain Python(asyncio)이 부른다 — 처음(0009)엔 LangGraph StateGraph + Send였다 (ADR 0030, HYO-220).
"""

import asyncio

from common import ops
from common.core import check_scope
from common.prompts import REVISIT_INSTRUCTIONS, condition_prompt
from common.schemas import Verdict

from .engines import as_runner, limit  # noqa: F401 — limit은 예전 자리(revisit.limit)에서 부르는 곳이 있다
from .tools import REVISIT_TOOLS


def revisit_flow(ctx, model):
    """→ 흐름 하나 (`await flow(now)` → {report, p3})."""
    runner = as_runner(model)

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

    async def flow(now):
        adrs = ops.radar(ctx, "list", ctx.adr_dir, "--in-scope")
        rows = [ops.human_row(a, c) for a in adrs for c in a.get("revisit_when") or [] if c.get("check_type") == "human"]
        outs = await asyncio.gather(*(judge_node({"adr": a, "cond": c, "now": now})
                                      for a in adrs for c in a.get("revisit_when") or [] if c.get("check_type") != "human"))
        rows += [r for o in outs for r in o["rows"]]   # 사람 조건 줄 다음에 판정 줄 — 조건 순서대로 (decide가 받는 순서)
        p3 = [p for o in outs for p in o["p3"]]
        d = ops.decide(ctx, rows, now)
        return {"report": ops.revisit_report(ctx, d, now), "rows": rows, "p3": p3}

    return flow


async def revisit(ctx, model, now, model_name):
    check_scope(ctx)
    runner = as_runner(model)
    log = runner.attach_log(ctx, "revisit", model_name)
    s = await revisit_flow(ctx, runner)(now)
    return s["report"], {"p3_errors": s.get("p3") or [], **log.totals()}

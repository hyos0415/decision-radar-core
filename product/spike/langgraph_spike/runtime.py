"""런타임 스파이크 2번 — LangGraph (HYO-64, 기준 docs/design/runtime-spike.md). Agents SDK와 짝 비교.

revisit:  StateGraph — list(radar.py list) ─Send로 조건마다─▶ judge(create_agent + Verdict 네이티브 구조화 출력) ─▶ decide(radar.py decide --apply)
          judge는 LangGraph가 **병렬로** 돌린다 (Agents SDK 쪽은 코드가 순서대로 부른다 — 병렬은 직접 짜야 한다)
capture:  create_agent(match_adr · propose_adr) + HumanInTheLoopMiddleware(propose_adr: approve/reject) + SqliteSaver(checkpointer)
          1턴  승인 대기에서 interrupt → 체크포인트가 SQLite 파일에 남는다
          2턴  (새 프로세스, resume.py) 같은 파일·같은 thread_id → Command(resume={"decisions": [...]}) 재개

프롬프트·스키마·도구 본체·보고서는 common/ 한 벌이다. 여기는 LangChain·LangGraph 형식(@tool · create_agent · StateGraph)으로 감싸기만 한다.
도구는 실행 컨텍스트(Ctx)를 ToolRuntime.context로 받는다 — 런타임 컨텍스트는 체크포인트에 저장되지 않는다 (Agents SDK의 context_serializer → {}와 같은 효과).
구조화 출력은 ProviderStrategy를 **명시**한다 — 자동 선택에 맡기면 profile 없는 스텁은 ToolStrategy(강제 tool_choice)로 가서 실제 모델과 경로가 갈린다.
"""

import operator
import os
import sys
from typing import Annotated, TypedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
os.environ["LANGSMITH_TRACING"] = "false"  # P5 — LangSmith로 실행을 올리지 않는다
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import HumanInTheLoopMiddleware  # noqa: E402
from langchain.agents.structured_output import ProviderStrategy  # noqa: E402
from langchain.tools import ToolRuntime, tool  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, Send  # noqa: E402

from common import ops  # noqa: E402
from common.core import Ctx, check_scope  # noqa: E402
from common.prompts import CAPTURE_INSTRUCTIONS, REVISIT_INSTRUCTIONS, capture_prompt, condition_prompt  # noqa: E402
from common.schemas import AdrDraft, CaptureResult, Verdict  # noqa: E402

THREAD = {"configurable": {"thread_id": "capture"}}


def _limit(max_turns):
    return 2 * max_turns + 2  # create_agent 한 턴 = 모델 노드 + 도구 노드 두 단계


# ── 도구: common/ops 본체를 @tool로 ─────────────────────────────────

@tool("snapshot_fetch", description=ops.DOCS["snapshot_fetch"])
def snapshot_fetch(url: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.snapshot_fetch(runtime.context, url)


@tool("snapshot_search", description=ops.DOCS["snapshot_search"])
def snapshot_search(query: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.snapshot_search(runtime.context, query)


@tool("read_repo_file", description=ops.DOCS["read_repo_file"])
def read_repo_file(repo: str, path: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.read_repo_file(runtime.context, repo, path)


@tool("match_adr", description=ops.DOCS["match_adr"])
def match_adr(technology: str, repo: str, component: str, use: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.match_adr(runtime.context, technology, repo, component, use)


@tool("propose_adr", description=ops.DOCS["propose_adr"])
def propose_adr(draft: AdrDraft, runtime: ToolRuntime[Ctx]) -> str:
    d = draft if isinstance(draft, AdrDraft) else AdrDraft.model_validate(draft)
    return ops.propose_adr(runtime.context, d)


REVISIT_TOOLS = [snapshot_fetch, snapshot_search, read_repo_file]


def _usage(messages):
    return [{"input_tokens": (m.usage_metadata or {}).get("input_tokens", 0), "output_tokens": (m.usage_metadata or {}).get("output_tokens", 0)}
            for m in messages if isinstance(m, AIMessage)]


# ── revisit ──────────────────────────────────────────────────────────

class RevisitState(TypedDict, total=False):
    now: str
    adrs: list
    rows: Annotated[list, operator.add]  # judge 병렬 가지들의 결과가 여기로 합쳐진다
    p3: Annotated[list, operator.add]
    usage: Annotated[list, operator.add]
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
                                          context=ctx, config={"recursion_limit": _limit(ops.REVISIT_MAX_TURNS)})
        except Exception as e:  # 400·파싱 실패·recursion limit — 안전 실패 (R1)
            row, err = ops.error_row(adr, cond, e)
            return {"rows": [row], "p3": [err]}
        n_tools = sum(isinstance(m, ToolMessage) for m in r["messages"])  # 병렬이라 ctx.calls 대신 이 판정의 메시지로 센다
        err = [] if n_tools else [{"adr": adr["id"], "condition_id": cond.get("id"), "kind": "tool_call_missing"}]
        return {"rows": [ops.verdict_row(adr, cond, r["structured_response"])], "p3": err, "usage": _usage(r["messages"])}

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


async def revisit(ctx, model, now, settings=None):
    check_scope(ctx)
    s = await revisit_graph(ctx, model).ainvoke({"now": now, "rows": [], "p3": [], "usage": []})
    u = s.get("usage") or []
    return s["report"], {"p3_errors": s.get("p3") or [], "model_calls": len(u),
                         "input_tokens": sum(x["input_tokens"] for x in u), "output_tokens": sum(x["output_tokens"] for x in u)}


# ── capture ──────────────────────────────────────────────────────────

def capture_agent(model, checkpointer, gate=True):
    mw = [HumanInTheLoopMiddleware(interrupt_on={"propose_adr": {"allowed_decisions": ["approve", "reject"]}})] if gate else []
    return create_agent(model, tools=[match_adr, propose_adr], system_prompt=CAPTURE_INSTRUCTIONS,
                        response_format=ProviderStrategy(CaptureResult), middleware=mw, checkpointer=checkpointer,
                        context_schema=Ctx, name="capture")


def _interrupts(r):
    return list(r.get("__interrupt__") or [])


def _report(ctx, now, r):
    its = _interrupts(r)
    if its:
        preview = [{"tool": a["name"], "arguments": a["args"]} for it in its for a in (it.value or {}).get("action_requests", [])]
        return ops.capture_report(ctx, now, link_candidates=[], awaiting=True, preview=preview)
    out = r.get("structured_response")
    return ops.capture_report(ctx, now, link_candidates=list(out.link_candidates) if out else [], awaiting=False,
                              note=out.note if out else None)


async def capture_turn1(ctx, model, conversation, now, state_path, settings=None, gate=True):
    ctx.now, ctx.calls = now, 0
    with SqliteSaver.from_conn_string(state_path) as cp:
        r = capture_agent(model, cp, gate).invoke({"messages": [{"role": "user", "content": capture_prompt(conversation, now)}]},
                                                   config={**THREAD, "recursion_limit": _limit(ops.CAPTURE_MAX_TURNS)}, context=ctx)
    diag = {"usage": _usage(r["messages"]),
            "p3_errors": [] if ops.matched(ctx) else [{"kind": "tool_call_missing", "message": "match_adr를 부르지 않음"}]}
    return _report(ctx, now, r), diag


def capture_turn2(ctx, model, state_path, decision, now):
    """새 프로세스에서 부른다. decision: approve | reject — 사람의 답을 여기로만 넣는다."""
    ctx.now, ctx.calls = now, 0
    with SqliteSaver.from_conn_string(state_path) as cp:
        agent = capture_agent(model, cp)
        before = agent.get_state(THREAD)
        seen = len(before.values.get("messages") or [])
        pending = list(before.interrupts)
        answer = {"type": "approve"} if decision == "approve" else {"type": "reject", "message": "사용자가 이 초안을 거부했다. 저장하지 않는다."}
        resume = {it.id: {"decisions": [answer] * len((it.value or {}).get("action_requests", []))} for it in pending}
        r = agent.invoke(Command(resume=resume), config={**THREAD, "recursion_limit": _limit(ops.CAPTURE_MAX_TURNS)}, context=ctx)
    return _report(ctx, now, r), {"usage": _usage(r["messages"][seen:]), "pending_restored": len(pending)}


# ── eval 어댑터가 부르는 런타임 인터페이스 ────────────────────────────

class Runtime:
    name = "langgraph"
    real_model = "anthropic"
    state_filename = "capture-checkpoint.sqlite"

    def add_args(self, ap):
        pass

    def real_spec(self, a):
        return {"kind": "anthropic"}

    def label(self, spec):
        return "anthropic"

    def new_shapes(self, spec):
        if spec["kind"] == "stub":
            return None
        import models
        return models.RequestShapes()

    def make_model(self, spec, shapes, flow):
        if spec["kind"] == "stub":
            from stub_model import StubChat
            return StubChat(script=spec["script"], flow=flow)
        import models
        return models.make_anthropic(shapes)

    def settings(self, spec):
        return None  # effort는 모델 생성자에 (models.make_anthropic)

    revisit = staticmethod(revisit)
    capture_turn1 = staticmethod(capture_turn1)

    def resume_cmd(self, spec, state_path, decision, now, radar_py, out_dir):
        cmd = [sys.executable, os.path.join(HERE, "resume.py"), "--state", state_path, "--decision", decision, "--now", now,
               "--radar", radar_py, "--out", out_dir]
        return cmd + (["--stub-script", spec["script_file"]] if spec["kind"] == "stub" else ["--anthropic"])

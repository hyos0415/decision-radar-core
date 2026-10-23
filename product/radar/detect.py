"""detect 그래프 — 스킬 SKILL.md 흐름 2와 같은 명세 (R10·R14·R16·R17). 새로 만들었다 (HYO-43).

  load ─▶ triage ─▶ screen ─Send로 후보마다─▶ draft ─▶ write ─▶ (validate 실패가 있으면) repair ─▶ report
  (코드)  (모델)    (코드)                    (모델)   (코드)                              (모델+코드)

  load    대상 repo의 CLAUDE.md·manifest 읽기 + 소식 수집(radar.py snapshot feed)
  triage  모델이 소식을 거른다 — 후보(technology·affected·dependency)와 버린 것(이유). 의존성 릴리스는 breaking·security·major만 (A5b)
  screen  R10(affected 하나 이상·실재 repo)과 R14(기존 ADR과 겹침 → skipped_existing)를 **코드가** 집행한다. 겹침 판정은 radar.py match
  draft   모델이 후보마다 초안 서술(title·use·context·decision·evidence·revisit_when)을 쓴다. 병렬
  write   초안 파일은 코드가 쓴다 — ops.propose_adr: next-id → proposed로 렌더 → radar.py validate
  repair  validate가 거부한 초안을 모델이 오류와 함께 **한 번** 고쳐 쓴다(서술만). 파일은 코드가 같은 번호로 다시 쓰고 validate를 다시 돈다

status를 쓰는 노드는 없다 (R8): AdrDraft에 status 칸이 없고 ops.render가 proposed로 박는다. 모델에게 주는 도구는 읽기(snapshot_*)뿐이다.
기존 ADR은 읽기만 한다 — radar.py match가 읽고, write는 새 번호의 새 파일만 만든다 (INV-1).
"""

import json
import operator
import uuid
from typing import Annotated, TypedDict

from langchain.agents import create_agent
from langchain.agents.structured_output import ProviderStrategy
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from common import ops
from common.core import Ctx, check_scope
from common.schemas import AdrDraft

from . import detect_ops, hitl, llm_log
from .detect_prompts import DRAFT_INSTRUCTIONS, REPAIR_INSTRUCTIONS, TRIAGE_INSTRUCTIONS, draft_prompt, repair_prompt, triage_prompt
from .detect_schemas import DraftBody, Triage
from .revisit import limit
from .tools import DETECT_TOOLS

DETECT_MAX_TURNS = 8  # 소식 몇 건을 열어 보고 답 한 번이면 끝난다. 넘으면 루프로 보고 오류로 센다 (P3)


class DetectState(TypedDict, total=False):
    now: str
    stack: str
    feed: list
    excluded: list
    kept: list
    skipped: list
    dropped: list
    drafts: Annotated[list, operator.add]  # draft 병렬 가지들의 결과
    written: list
    p3: Annotated[list, operator.add]
    report: dict


def _item_for(feed, urls):
    return next((it for it in feed if it.get("url") in urls), None)


def detect_graph(ctx, model):
    triage_agent = create_agent(model, tools=DETECT_TOOLS, system_prompt=TRIAGE_INSTRUCTIONS,
                                response_format=ProviderStrategy(Triage), context_schema=Ctx, name="detect-triage")
    repair_agent = create_agent(model, tools=DETECT_TOOLS, system_prompt=REPAIR_INSTRUCTIONS,
                                response_format=ProviderStrategy(DraftBody), context_schema=Ctx, name="detect-repair")
    draft_agent = create_agent(model, tools=DETECT_TOOLS, system_prompt=DRAFT_INSTRUCTIONS,
                               response_format=ProviderStrategy(DraftBody), context_schema=Ctx, name="detect-draft")

    run = {"id": None}

    def load_node(state):
        ctx.now, ctx.calls = state["now"], 0
        run["id"] = f"detect-{state['now']}-{uuid.uuid4().hex[:8]}"  # 이 실행의 초안들이 같이 갖는 기록 — 같은 실행의 내보내기를 지문에서 넘기는 근거 (hitl._own_exports)
        return {"stack": detect_ops.read_stack(ctx), "feed": detect_ops.snapshot_feed(ctx)}

    async def triage_node(state):
        feed = state["feed"]
        if not feed:  # 소식이 없으면 모델을 부르지 않는다 — 조용한 주가 정상이다 (R3)
            return {"kept": [], "skipped": [], "dropped": [], "excluded": []}
        try:
            r = await triage_agent.ainvoke({"messages": [{"role": "user", "content": triage_prompt(state["now"], state["stack"], feed)}]},
                                           context=ctx, config={"recursion_limit": limit(DETECT_MAX_TURNS)})
        except Exception as e:  # 400·파싱 실패·recursion limit — 후보 없음으로 두고 오류로 센다 (R1: 추측으로 채우지 않는다)
            return {"kept": [], "skipped": [], "dropped": [], "excluded": [],
                    "p3": [{"kind": "model_error", "stage": "triage", "type": type(e).__name__, "message": str(e)[:300]}]}
        t = r["structured_response"]
        cands = [c.model_dump() for c in t.candidates]
        kept, skipped, dropped = detect_ops.screen(ctx, cands, state["feed"])
        return {"kept": kept, "skipped": skipped, "dropped": dropped, "excluded": [e.model_dump() for e in t.excluded]}

    def fan_out(state):
        sends = [Send("draft", {"now": state["now"], "w": w, "item": _item_for(state["feed"], w["candidate"]["source_urls"])})
                 for w in state["kept"]]
        return sends or ["write"]

    async def draft_node(inp):
        w = inp["w"]
        cand = w["candidate"]
        related = [{k: r[k] for k in ("id", "status", "technology", "use")} for r in w["related"]]
        try:
            r = await draft_agent.ainvoke({"messages": [{"role": "user", "content": draft_prompt(inp["now"], cand, inp["item"], related)}]},
                                          context=ctx, config={"recursion_limit": limit(DETECT_MAX_TURNS)})
        except Exception as e:
            return {"p3": [{"kind": "model_error", "stage": "draft", "technology": cand["technology"], "type": type(e).__name__,
                            "message": str(e)[:300]}]}
        return {"drafts": [{"w": w, "draft": r["structured_response"].model_dump()}]}

    def write_node(state):
        written = []
        for d in sorted(state.get("drafts") or [], key=lambda x: x["w"]["idx"]):  # 병렬 가지의 도착 순서와 무관하게 소식 순서대로 번호를 받는다
            w, cand = d["w"], d["w"]["candidate"]
            body = detect_ops.compose(d["draft"], w)  # 후보의 정체·related는 코드가 덮어쓴다 — draft는 서술만 한다
            ctx.calls += 1
            item_id = hitl.new_item_id(ctx, "detect", cand["technology"])  # 별도 파일은 초안 파일을 쓰는 순간에 생긴다 (설계 §3-1)
            res = hitl.write_adr(ctx, item_id, "detect", AdrDraft.model_validate(body), lifecycle="REVIEW_CANDIDATE",
                                 final_lifecycle="REVIEW_CANDIDATE", by="graph", hold_export=True, run_id=run["id"])  # 내보내기를 켰으면 Inbox 검토까지 로컬에 둔다 (결정 8)
            written.append({"candidate": cand, "w": w, "body": d["draft"], "id": res["id"], "path": res["written"], "item": item_id,
                            "validate_errors": res["validate_errors"], "repaired": False})
        return {"written": written}

    def after_write(state):
        return "repair" if any(x["validate_errors"] for x in state["written"]) else "report"

    async def repair_node(state):
        """validate가 거부한 초안을 모델에게 오류와 함께 **한 번** 고쳐 쓰게 한다 (스킬의 「초안 파일만 고친다」와 같다).
        모델은 서술(DraftBody)만 낸다 — 파일은 코드가 같은 번호로 다시 쓰고 radar.py validate를 다시 돌린다. 그래도 실패면 draft_invalid로 센다."""
        p3, out = [], []
        for x in state["written"]:
            if not x["validate_errors"]:
                out.append(x)
                continue
            item = _item_for(state["feed"], x["candidate"]["source_urls"])
            try:
                r = await repair_agent.ainvoke({"messages": [{"role": "user", "content": repair_prompt(
                    state["now"], x["candidate"], item, x["body"], x["validate_errors"])}]},
                    context=ctx, config={"recursion_limit": limit(DETECT_MAX_TURNS)})
                body = r["structured_response"].model_dump()
            except Exception as e:
                p3.append({"kind": "model_error", "stage": "repair", "adr": x["id"], "type": type(e).__name__, "message": str(e)[:300]})
                out.append(x)
                continue
            errs = detect_ops.rewrite(ctx, x["path"], x["id"], AdrDraft.model_validate(detect_ops.compose(body, x["w"])), item_id=x.get("item"))
            out.append({**x, "body": body, "validate_errors": errs, "repaired": True})
        return {"written": out, "p3": p3}

    def report_node(state):
        failed = [p for p in state.get("p3") or [] if p.get("stage") == "draft"]
        rep = detect_ops.detect_report(ctx, state["now"], skipped=state.get("skipped") or [], dropped=state.get("dropped") or [],
                                       excluded=state.get("excluded") or [], written=state.get("written") or [], failed=failed)
        return {"report": rep}

    g = StateGraph(DetectState)
    g.add_node("load", load_node)
    g.add_node("triage", triage_node)
    g.add_node("draft", draft_node)
    g.add_node("write", write_node)
    g.add_node("repair", repair_node)
    g.add_node("report", report_node)
    g.add_edge(START, "load")
    g.add_edge("load", "triage")
    g.add_conditional_edges("triage", fan_out, ["draft", "write"])
    g.add_edge("draft", "write")
    g.add_conditional_edges("write", after_write, ["repair", "report"])
    g.add_edge("repair", "report")
    g.add_edge("report", END)
    return g.compile()


async def detect(ctx, model, now, model_name):
    check_scope(ctx)
    log = llm_log.attach(model, ctx, "detect", model_name)
    s = await detect_graph(ctx, model).ainvoke({"now": now, "drafts": [], "p3": []})
    p3 = list(s.get("p3") or [])
    p3 += [{"kind": "draft_invalid", "adr": c["adr"], "errors": c["validate_errors"]}
           for c in s["report"]["candidates"] if c["validate_errors"]]
    return s["report"], {"p3_errors": p3, **log.totals()}

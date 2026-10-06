"""capture 그래프 — HITL·체크포인트·재개 (설계 docs/design/hitl-state.md, HYO-44).

create_agent(match_adr · propose_adr) + HumanInTheLoopMiddleware(propose_adr: approve/edit/reject) + SqliteSaver
  1턴  승인 대기에서 interrupt → 체크포인트가 SQLite 파일에 남는다. 이 시점의 지문(Fingerprint)도 같은 파일의 별도 표에 남는다
       — 별도 파일(.radar/items)은 아직 없다 (R9: 승인 전 ADR repo 쓰기 0, 설계 §3-1)
  2턴  (새 프로세스, resume.py) 같은 파일·같은 thread_id → 재개 검사(hitl.check_resume) → Command(resume=...)
       승인 순간에 별도 파일이 생긴다 (write-ahead 의도와 같은 단계, tools.propose_adr → hitl.write_adr)

스파이크에서 바뀐 것: thread_id = item-id(결정 5, 더는 "capture" 고정이 아니다) · edit 채널(결정 6) · 재개 검사(결정 3)
· write-ahead 쓰기(결정 4) · 끝난 항목의 checkpoint 삭제. 지문 구현은 Q2 미정이라 기본값(A)이다 — 사용자가 다음 대화에서 정한다.
"""

import dataclasses
import hashlib

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.agents.structured_output import ProviderStrategy
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from common import ops
from common.core import Ctx
from common.prompts import CAPTURE_INSTRUCTIONS, capture_prompt
from common.schemas import AdrDraft, CaptureResult

from . import hitl, llm_log
from .revisit import limit
from .tools import match_adr, propose_adr

DECISIONS = ["approve", "edit", "reject"]  # 결정 6 — 스파이크는 approve/reject였다


def thread_config(item_id, **configurable):
    """thread_id = item-id (결정 5). configurable의 나머지는 도구가 읽는 승인 정보 — checkpoint에 저장되지 않는다."""
    return {"configurable": {"thread_id": item_id, **configurable}}


def capture_agent(model, checkpointer, gate=True):
    mw = [HumanInTheLoopMiddleware(interrupt_on={"propose_adr": {"allowed_decisions": DECISIONS}})] if gate else []
    return create_agent(model, tools=[match_adr, propose_adr], system_prompt=CAPTURE_INSTRUCTIONS,
                        response_format=ProviderStrategy(CaptureResult), middleware=mw, checkpointer=checkpointer,
                        context_schema=Ctx, name="capture")


def _interrupts(r):
    return list(r.get("__interrupt__") or [])


def _preview(its):
    return [{"tool": a["name"], "arguments": a["args"]} for it in its for a in (it.value or {}).get("action_requests", [])]


def _report(ctx, now, r, item_id=None, extra=None):
    # ops.capture_report는 ctx.events가 전부 도구 호출이라고 가정한다 — llm_call 줄을 뺀 보기로 부른다
    ctx = dataclasses.replace(ctx, events=[e for e in ctx.events if e.get("type") == "assistant"])
    its = _interrupts(r)
    if its:
        rep = ops.capture_report(ctx, now, link_candidates=[], awaiting=True, preview=_preview(its))
    else:
        out = r.get("structured_response")
        rep = ops.capture_report(ctx, now, link_candidates=list(out.link_candidates) if out else [], awaiting=False,
                                 note=out.note if out else None)
    return _tag(rep, item_id, extra)


def _tag(rep, item_id, extra=None):
    if item_id:
        rep["item_id"] = item_id
    if extra:
        rep["hitl"] = extra
    return rep


def _plain_report(ctx, now, *, awaiting, preview=None, note=None, item_id=None, extra=None):
    ctx = dataclasses.replace(ctx, events=[e for e in ctx.events if e.get("type") == "assistant"])
    return _tag(ops.capture_report(ctx, now, link_candidates=[], awaiting=awaiting, preview=preview, note=note), item_id, extra)


def _pending_draft(its):
    for it in its:
        for a in (it.value or {}).get("action_requests", []):
            if a["name"] == "propose_adr":
                return a["args"].get("draft")
    return None


def make_item_id(ctx, conversation, store):
    """대화 하나 = 항목 하나. 같은 대화를 또 넣어도 대기 중인 항목을 덮지 않는다 (-2, -3 …)."""
    return hitl.new_item_id(ctx, "capture", hashlib.sha1(conversation.encode("utf-8")).hexdigest()[:8], taken=set(store.threads()))


async def capture_turn1(ctx, model, conversation, now, state_path, model_name, gate=True, item_id=None, fingerprint=None):
    ctx.now, ctx.calls = now, 0
    store = hitl.StateStore(state_path)
    item_id = item_id or make_item_id(ctx, conversation, store)
    log = llm_log.attach(model, ctx, "capture", model_name)
    with SqliteSaver.from_conn_string(state_path) as cp:
        r = capture_agent(model, cp, gate).invoke({"messages": [{"role": "user", "content": capture_prompt(conversation, now)}]},
                                                   config={**thread_config(item_id), "recursion_limit": limit(ops.CAPTURE_MAX_TURNS)},
                                                   context=ctx)
    its = _interrupts(r)
    if its:  # 멈춘 순간의 세상을 적어 둔다 — 재개 때 비교한다. ADR repo가 아니라 checkpoint 파일 안이라 R9에 걸리지 않는다
        scope = hitl.scope_of(_pending_draft(its) or {})
        fp = hitl.fingerprint_for(ctx, fingerprint)
        store.put(item_id, fp.compute(ctx, scope), scope)
    diag = {**log.totals(), "usage": log.calls,
            "p3_errors": [] if ops.matched(ctx) else [{"kind": "tool_call_missing", "message": "match_adr를 부르지 않음"}]}
    return _report(ctx, now, r, item_id), diag


def _finish_thread(state_path, store, item_id):
    """항목이 끝났다 — checkpoint thread와 지문을 지운다 (§4-4). 별도 파일은 history와 함께 남는다."""
    with SqliteSaver.from_conn_string(state_path) as cp:
        cp.delete_thread(item_id)
    store.delete(item_id)


def _answer(decision, edited_draft):
    if decision == "approve":
        return {"type": "approve"}
    if decision == "edit":
        return {"type": "edit", "edited_action": {"name": "propose_adr", "args": {"draft": edited_draft}}}
    return {"type": "reject", "message": "사용자가 이 초안을 거부했다. 저장하지 않는다."}


def capture_turn2(ctx, model, state_path, decision, now, model_name, *, item_id=None, edited=None, stale_answer=None, fingerprint=None):
    """새 프로세스에서 부른다. decision: approve | edit | reject — 사람의 답을 여기로만 넣는다.

    edit이면 edited(고친 초안 dict)가 필요하다. 쓰기 전에 hitl.check_edit이 검사하고, 실패하면 아무것도 쓰지 않고 오류를 보고서(`hitl.edit_errors`)에
    담아 다시 승인 대기로 돌려준다 (interrupt는 그대로 남는다).
    지문이 달라졌으면 재개하지 않고 `hitl.stale`에 무엇이 바뀌었는지 담아 다시 승인을 기다린다 — 답은 stale_answer: continue | rejudge | discard.
    """
    ctx.now, ctx.calls = now, 0
    log = llm_log.attach(model, ctx, "capture", model_name)
    store = hitl.StateStore(state_path)
    if item_id is None:
        threads = store.threads()
        if len(threads) != 1:
            raise hitl.HitlError(f"item_id가 필요하다 — 대기 중인 항목 {threads}")
        item_id = threads[0]
    cfg = thread_config(item_id)

    with SqliteSaver.from_conn_string(state_path) as cp:
        agent = capture_agent(model, cp)
        before = agent.get_state(cfg)
        pending = list(before.interrupts)
    diag = {"usage": log.calls, "pending_restored": len(pending)}
    preview = _preview(pending)
    original = _pending_draft(pending)
    stored_fp, scope = store.get(item_id)
    if scope is None:
        scope = hitl.scope_of(original or {})

    def stop(note, **x):  # 항목을 재개하지 않고 돌려준다
        return _plain_report(ctx, now, awaiting=x.pop("awaiting", False), preview=x.pop("preview", None), note=note,
                             item_id=item_id, extra=x), diag

    if decision not in ("approve", "edit", "reject"):
        raise hitl.HitlError(f"모르는 답 {decision!r}")
    if stale_answer not in (None, *hitl.STALE_ANSWERS):
        raise hitl.HitlError(f"모르는 stale 답 {stale_answer!r}")

    edited_draft, approval = None, {"decision": decision}
    if decision == "edit":
        draft, errs = hitl.check_edit(ctx, edited)
        if errs:  # 쓰지 않는다 — 사람이 다시 고친다
            return stop("고친 초안이 검사를 통과하지 못했다 — 쓰지 않았다. 다시 고쳐 달라", awaiting=True, preview=preview, edit_errors=errs)
        edited_draft = draft.model_dump()
        approval["draft_hash_original"] = hitl.sha(ops.render(AdrDraft.model_validate(original), ctx.now)) if original else None

    if decision != "reject":
        plan = hitl.check_resume(ctx, item_id, stored_fp=stored_fp, scope=scope)
        act = plan["action"]
        if act == "recovered":  # 쓰다 만 것을 회복했다 — 항목이 끝났다
            _finish_thread(state_path, store, item_id)
            return stop(f"회복: {plan['recovery']['action']}", recovery=plan["recovery"]["action"], validate_errors=plan["recovery"]["result"]["validate_errors"])
        if act == "done":
            _finish_thread(state_path, store, item_id)
            return stop("이미 완료된 항목 — checkpoint만 정리했다", recovery="already_done")
        if act in ("blocked", "orphan"):  # 덮지 않고 사람에게 보인다 — checkpoint는 남긴다
            return stop(f"멈춤: {plan.get('reason')}", awaiting=False, blocked={k: v for k, v in plan.items() if k != "recovery"})
        if act == "stale":
            if stale_answer is None:
                return stop("승인 대기 중에 ADR repo가 바뀌었다 — 계속·다시 판정·버림 중에 고른다", awaiting=True, preview=preview,
                            stale={"changes": plan["changes"], "answers": list(hitl.STALE_ANSWERS)})
            if stale_answer == "discard":
                hitl.close_item(ctx, item_id, "discard")
                _finish_thread(state_path, store, item_id)
                return stop("버렸다 — 아무것도 쓰지 않았다", outcome="discard")
            if stale_answer == "rejudge":  # 같은 대화를 새 세상에서 다시 돌린다 — 호출자가 capture_turn1을 다시 부른다
                hitl.close_item(ctx, item_id, "rejudge")
                _finish_thread(state_path, store, item_id)
                return stop("다시 판정한다 — 이 항목은 닫았다. 같은 대화로 capture_turn1을 다시 부른다", outcome="rejudge")
        approval["fingerprint"] = plan["new_fp"]  # 「계속」이거나 그대로 — 지금 세상이 승인의 근거다
    if decision == "approve" and not pending:
        raise hitl.HitlError(f"{item_id}: 대기 중인 승인이 없다")

    answer = _answer(decision, edited_draft)
    resume = {it.id: {"decisions": [answer] * len((it.value or {}).get("action_requests", []))} for it in pending}
    cfg = thread_config(item_id, radar_approval=approval)
    with SqliteSaver.from_conn_string(state_path) as cp:
        r = capture_agent(model, cp).invoke(Command(resume=resume), config={**cfg, "recursion_limit": limit(ops.CAPTURE_MAX_TURNS)}, context=ctx)
    item = hitl.read_item(ctx, item_id)
    if decision == "reject" or (item and (item.get("write_intent") or {}).get("state") == "done"):
        _finish_thread(state_path, store, item_id)  # 거부(별도 파일 없음)·쓰기 완료 — 끝난 항목
    return _report(ctx, now, r, item_id), {**diag, "usage": log.calls}

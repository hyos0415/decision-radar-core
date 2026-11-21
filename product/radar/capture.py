"""capture 흐름 — HITL·멈춤·재개 (설계 docs/design/hitl-state.md, HYO-44).

에이전트 루프(loop.py, 도구: snapshot_fetch(가드) · match_adr · propose_adr) — propose_adr 호출이 나오면 실행하지 않고 멈춘다 (approve/edit/reject)
  1턴  승인 대기에서 멈춤 → 대화와 대기 중인 호출이 SQLite 파일에 남는다(hitl.ThreadStore). 이 시점의 지문(Fingerprint)도 같은 파일의 별도 표에 남는다
       — 별도 파일(.radar/items)은 아직 없다 (R9: 승인 전 ADR repo 쓰기 0, 설계 §3-1)
  2턴  (새 프로세스, resume.py) 같은 파일·같은 thread_id → 재개 검사(hitl.check_resume) → 사람의 답대로 대기 중인 호출을 실행하고 루프를 이어 간다
       승인 순간에 별도 파일이 생긴다 (write-ahead 의도와 같은 단계, tools.propose_adr → hitl.write_adr)

처음(0009)엔 LangChain create_agent + HumanInTheLoopMiddleware + LangGraph SqliteSaver였다 (ADR 0030, HYO-220). 멈출 때 저장하는 것은
대화(메시지 목록 JSON) · 대기 중인 호출 · 멈춘 시각뿐이다 — 재개 검사 · 쓰기 순서 · 검증은 처음부터 hitl.py가 했다.
스파이크에서 바뀐 것: thread_id = item-id(결정 5, 더는 "capture" 고정이 아니다) · edit 채널(결정 6) · 재개 검사(결정 3)
· write-ahead 쓰기(결정 4) · 끝난 항목의 멈춘 대화 삭제 · 지문 A′(결정 7) · git 내보내기(결정 8 — 켰을 때만. 재개 전에 원격을 따라잡고,
쓰기가 끝나면 커밋·push. 재개 검사에서 또 달라졌으면 커밋하지 않고 항목에 남긴다 → review.py로 사람이 답한다).
"""

import dataclasses
import hashlib

from common import ops
from common.prompts import CAPTURE_INSTRUCTIONS, capture_prompt
from common.schemas import AdrDraft, CaptureResult

from . import hitl, llm_log, loop
from .tools import CAPTURE_TOOLS

DECISIONS = ["approve", "edit", "reject"]  # 결정 6 — 스파이크는 approve/reject였다
GATED = ("propose_adr",)
REJECT_MESSAGE = "사용자가 이 초안을 거부했다. 저장하지 않는다."


def pin_repo(repo):
    """사람이 정한 repo(빠른 capture의 repo 칸, HYO-95)를 propose_adr 초안의 affected에 박는다 — component는 모델 것을 둔다.
    루프가 멈추기 **전에** 호출을 고친다 → Inbox에 처음 보이는 초안부터 정한 repo이고, 사람의 「승인」은 승인으로 남는다(edit로 바꿔 보내지 않는다).
    repo가 없으면 None(고치지 않는다)."""
    if not repo:
        return None

    def rewrite(block):
        if block.get("name") != "propose_adr" or not isinstance((block.get("input") or {}).get("draft"), dict):
            return block
        d = dict(block["input"]["draft"])
        comps = list(dict.fromkeys((a or {}).get("component") for a in d.get("affected") or [] if (a or {}).get("component")))
        d["affected"] = [{"repo": repo, "component": x} for x in comps]  # component가 없으면 빈 목록 → validate가 막는다
        return {**block, "input": {**block["input"], "draft": d}}
    return rewrite


def _run(model, conv, ctx, config, gate=True, repo=None):
    return loop.run(model, name="capture", system=CAPTURE_INSTRUCTIONS, conv=conv, schema=CaptureResult, tools=CAPTURE_TOOLS, ctx=ctx,
                    max_turns=ops.CAPTURE_MAX_TURNS, config=config, interrupt_on=GATED if gate else (), rewrite=pin_repo(repo))


def _preview(calls):
    return [{"tool": c["name"], "arguments": c["input"]} for c in calls]


def _report(ctx, now, kind, val, item_id=None, extra=None):
    # ops.capture_report는 ctx.events가 전부 도구 호출이라고 가정한다 — llm_call 줄을 뺀 보기로 부른다
    ctx = dataclasses.replace(ctx, events=[e for e in ctx.events if e.get("type") == "assistant"])
    if kind == "interrupt":
        rep = ops.capture_report(ctx, now, link_candidates=[], awaiting=True, preview=_preview(val))
    else:
        rep = ops.capture_report(ctx, now, link_candidates=list(val.link_candidates) if val else [], awaiting=False,
                                 note=val.note if val else None)
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


def pending_draft(calls):
    """대기 중인 호출 → propose_adr 초안 dict | None."""
    for c in calls or []:
        if c["name"] == "propose_adr":
            return (c.get("input") or {}).get("draft")
    return None


def pending_items(state_path):
    """대기 중인 capture 항목 → [{id, draft, since}] (멈춘 대화만 읽는다 — 모델을 부르지 않는다). 데스크톱 Inbox가 쓴다."""
    threads, out = hitl.ThreadStore(state_path), []
    for tid in threads.threads():
        t = threads.get(tid)
        draft = pending_draft((t or {}).get("pending"))
        if draft is not None:
            out.append({"id": tid, "draft": draft, "since": t["created_at"]})
    return out


def make_item_id(ctx, conversation, store):
    """대화 하나 = 항목 하나. 같은 대화를 또 넣어도 대기 중인 항목을 덮지 않는다 (-2, -3 …)."""
    return hitl.new_item_id(ctx, "capture", hashlib.sha1(conversation.encode("utf-8")).hexdigest()[:8], taken=set(store.threads()))


def _pause(state_path, item_id, conv, calls):
    hitl.ThreadStore(state_path).put(item_id, conv.to_json(), calls)


async def capture_turn1(ctx, model, conversation, now, state_path, model_name, gate=True, item_id=None, fingerprint=None, repo=None):
    ctx.now, ctx.calls = now, 0
    store = hitl.StateStore(state_path)
    item_id = item_id or make_item_id(ctx, conversation, store)
    log = llm_log.attach(model, ctx, "capture", model_name)
    conv = loop.Conversation.start(capture_prompt(conversation, now, repo))
    kind, val, conv = _run(model, conv, ctx, {"thread_id": item_id}, gate, repo)
    if kind == "interrupt":  # 멈춘 순간의 세상을 적어 둔다 — 재개 때 비교한다. ADR repo가 아니라 상태 파일 안이라 R9에 걸리지 않는다
        _pause(state_path, item_id, conv, val)
        scope = hitl.scope_of(pending_draft(val) or {})
        fp = hitl.fingerprint_for(ctx, fingerprint)
        store.put(item_id, fp.compute(ctx, scope), scope)
    diag = {**log.totals(), "usage": log.calls,
            "p3_errors": [] if ops.matched(ctx) else [{"kind": "tool_call_missing", "message": "match_adr를 부르지 않음"}]}
    return _report(ctx, now, kind, val, item_id), diag


def _finish_thread(state_path, store, item_id):
    """항목이 끝났다 — 멈춘 대화와 지문을 지운다 (§4-4). 별도 파일은 history와 함께 남는다."""
    hitl.ThreadStore(state_path).delete(item_id)
    store.delete(item_id)


def _decisions(pending, decision, edited_draft):
    """사람의 답 → 대기 중인 propose_adr 호출마다의 결정 (loop.run_tools의 decisions). 다른 호출은 그대로 실행한다."""
    out = {}
    for c in pending:
        if c["name"] not in GATED:
            continue
        if decision == "edit":
            out[c["id"]] = ("edit", {**(c.get("input") or {}), "draft": edited_draft})
        elif decision == "reject":
            out[c["id"]] = ("reject", REJECT_MESSAGE)
        else:
            out[c["id"]] = ("approve", None)
    return out


def capture_turn2(ctx, model, state_path, decision, now, model_name, *, item_id=None, edited=None, stale_answer=None, fingerprint=None):
    """새 프로세스에서 부른다. decision: approve | edit | reject — 사람의 답을 여기로만 넣는다.

    edit이면 edited(고친 초안 dict)가 필요하다. 쓰기 전에 hitl.check_edit이 검사하고, 실패하면 아무것도 쓰지 않고 오류를 보고서(`hitl.edit_errors`)에
    담아 다시 승인 대기로 돌려준다 (멈춘 대화는 그대로 남는다).
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

    saved = hitl.ThreadStore(state_path).get(item_id)
    pending = list((saved or {}).get("pending") or [])
    diag = {"usage": log.calls, "pending_restored": len(pending)}
    preview = _preview(pending)
    original = pending_draft(pending)
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

    if decision == "approve" and original is not None:  # 대상 밖 repo(예: 모델이 지은 unknown)·빈 affected면 쓰지 않는다 — 사람이 고쳐서 승인한다 (R18)
        errs = hitl.affected_errors(ctx, original)
        if errs:
            return stop("affected가 비었거나 레이더 대상이 아니다 — 쓰지 않았다. repo·component를 고쳐서 승인해 달라", awaiting=True, preview=preview, edit_errors=errs)

    if decision != "reject":
        blocked = hitl.sync_exporter(ctx)  # 내보내기를 켰으면 원격을 먼저 따라잡는다 — 사람이 그 사이 push했으면 지문 비교가 그 세상을 본다 (결정 8)
        if blocked:
            return stop(f"멈춤: 원격 동기화 실패 ({blocked.get('reason')})", awaiting=True, preview=preview, blocked=blocked)
        plan = hitl.check_resume(ctx, item_id, stored_fp=stored_fp, scope=scope)
        act = plan["action"]
        if act == "recovered":  # 쓰다 만 것을 회복했다 — 항목이 끝났다
            _finish_thread(state_path, store, item_id)
            return stop(f"회복: {plan['recovery']['action']}", recovery=plan["recovery"]["action"], validate_errors=plan["recovery"]["result"]["validate_errors"])
        if act == "done":
            _finish_thread(state_path, store, item_id)
            return stop("이미 완료된 항목 — 멈춘 대화만 정리했다", recovery="already_done")
        if act in ("blocked", "orphan"):  # 덮지 않고 사람에게 보인다 — 멈춘 대화는 남긴다
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

    conv = loop.Conversation.from_json(saved["conversation"]) if saved else loop.Conversation()
    decisions = _decisions(pending, decision, edited_draft)
    if decision == "edit":  # 고친 초안을 대화에도 반영한다 — 모델이 다음 턴에 실제로 쓰인 초안을 본다
        for b in (conv.messages[-1]["content"] if conv.messages and conv.messages[-1]["role"] == "assistant" else []):
            if b.get("id") in decisions:
                b["input"] = decisions[b["id"]][1]
    config = {"thread_id": item_id, "radar_approval": approval}
    if pending:
        loop.run_tools(conv, pending, CAPTURE_TOOLS, ctx, config, decisions)
    kind, val, conv = _run(model, conv, ctx, config)
    if kind == "interrupt":  # 승인 뒤 모델이 또 초안을 냈다 — 다시 대기로 둔다
        _pause(state_path, item_id, conv, val)
    item = hitl.read_item(ctx, item_id)
    if kind != "interrupt" and (decision == "reject" or (item and (item.get("write_intent") or {}).get("state") == "done")):
        _finish_thread(state_path, store, item_id)  # 거부(별도 파일 없음)·쓰기 완료 — 끝난 항목
    exp = (item or {}).get("export")  # 내보내기를 쓴 항목만 — stale·blocked면 review.py로 이어 간다 (결정 8)
    return _report(ctx, now, kind, val, item_id, {"export": exp} if exp else None), {**diag, "usage": log.calls}

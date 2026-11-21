"""detect 흐름 — 스킬 SKILL.md 흐름 2와 같은 명세 (R10·R14·R16·R17). 새로 만들었다 (HYO-43).

  load ─▶ triage ─▶ screen ─후보마다 병렬─▶ draft ─▶ write ─▶ (validate 실패가 있으면) repair ─▶ report
  (코드)  (모델)    (코드)                  (모델)   (코드)                              (모델+코드)

순서는 plain Python(asyncio)이 부른다 — 처음(0009)엔 LangGraph StateGraph + Send였다. 단계 함수와 그 안의 규칙 집행은 그대로이고
부르는 순서 · 병렬만 코드가 직접 한다 (ADR 0030, HYO-220). 단계 이름은 STEPS — test_product가 설계와 대조한다.

  load    대상 repo의 CLAUDE.md·manifest 읽기 + 소식 수집(radar.py snapshot feed). 거부한 소식(HYO-142)·이미 반영된 릴리스(HYO-162)는 코드가 뺀다
  triage  모델이 소식을 거른다 — 후보(technology·affected·dependency)와 버린 것(이유). 의존성 릴리스는 breaking·security·major만 (A5b)
  screen  R16(매핑에 있는 기술은 affected = CLAUDE.md 「의존성 → component」 매핑 그대로, HYO-246) · R10(affected 하나 이상·실재 repo ·
          폴더가 있는 repo) · R14(기존 ADR과 겹침 → skipped_existing)를 **코드가** 집행한다. 겹침 판정은 radar.py match
  draft   모델이 후보마다 초안 서술(title·use·context·decision·evidence·revisit_when)을 쓴다. 병렬
  write   초안 파일은 코드가 쓴다 — ops.propose_adr: next-id → proposed로 렌더 → radar.py validate
  repair  validate가 거부한 초안을 모델이 오류와 함께 **한 번** 고쳐 쓴다(서술만). 파일은 코드가 같은 번호로 다시 쓰고 validate를 다시 돈다

status를 쓰는 단계는 없다 (R8): AdrDraft에 status 칸이 없고 ops.render가 proposed로 박는다. 모델에게 주는 도구는 읽기(snapshot_*)뿐이다.
기존 ADR은 읽기만 한다 — radar.py match가 읽고, write는 새 번호의 새 파일만 만든다 (INV-1).
"""

import asyncio
import json
import operator
import uuid
from typing import Annotated, TypedDict

from common.core import check_scope
from common.schemas import AdrDraft

from . import detect_ops, hitl
from .detect_prompts import (DRAFT_INSTRUCTIONS, REPAIR_INSTRUCTIONS, RESCUE_INSTRUCTIONS, TRIAGE_INSTRUCTIONS, draft_prompt, repair_prompt,
                             rescue_prompt, triage_prompt)
from .detect_schemas import DraftBody, Rescued, Triage
from .engines import as_runner
from .tools import detect_tools

STEPS = ("load", "triage", "draft", "write", "repair", "report")  # 단계 — status를 쓰는 단계 · 승인을 건너뛰는 단계가 끼면 test_product가 잡는다
DETECT_MAX_TURNS = 8  # draft · repair — 소식 몇 건을 열어 보고 답 한 번이면 끝난다. 넘으면 루프로 보고 오류로 센다 (P3)
TRIAGE_MAX_TURNS = 24  # triage는 소식 전체(주 20~40건)를 본다 — 8이면 열기·코드 찾기를 합쳐 8번뿐이라 찾아볼 여유가 없었다 (HYO-205, W47 세 번 모두 검색 0회)


class DetectState(TypedDict, total=False):  # 흐름이 단계 사이에 넘기는 값. Annotated(operator.add) 칸은 단계 결과를 덮지 않고 이어 붙인다 (merge)
    now: str
    stack: str
    feed: list
    rejected: list  # 모델에 넘기지 않고 excluded 앞에 붙이는 소식 — 사람이 전에 거부한 것(HYO-142) · 관문 판정 뒤 이미 반영된 것(HYO-162)
    missing: list  # 폴더가 없는 대상 repo (HYO-246) — 그 repo를 가리키는 후보는 screen이 R10으로 뺀다
    excluded: list
    security: dict  # 관문이 security로 남긴 소식의 결과 (HYO-205)
    triage_notes: dict  # 거르기의 답을 코드가 맞춘 기록 — 붙인 소식 · 답 없던 소식 · 고친 이름 (HYO-206)
    kept: list
    skipped: list
    dropped: list
    drafts: Annotated[list, operator.add]  # draft 병렬 가지들의 결과
    written: list
    p3: Annotated[list, operator.add]
    report: dict


def _item_for(feed, urls):
    return next((it for it in feed if it.get("url") in urls), None)


_APPEND = {k for k, t in DetectState.__annotations__.items() if getattr(t, "__metadata__", None) == (operator.add,)}   # drafts · p3


def merge(state, update):
    """단계 결과를 흐름 상태에 합친다 — drafts · p3는 이어 붙이고 나머지는 덮는다 (LangGraph 리듀서가 하던 일)."""
    for k, v in (update or {}).items():
        state[k] = list(state.get(k) or []) + list(v or []) if k in _APPEND else v
    return state


def detect_flow(ctx, model):
    """→ 흐름 하나 (`await flow(now)` → 상태 dict). 단계 함수는 `flow.steps`."""
    runner = as_runner(model)  # 에이전트 셋(triage·draft·repair)을 돌리는 실행 엔진 (engines.py, HYO-99)

    def agent(name, system, schema, user):
        first = name in ("detect-triage", "detect-rescue")  # 우리 코드를 찾아보는 것은 거르기 쪽만 — 초안 쪽은 그 결과를 받는다
        return runner.run(name=name, system=system, user=user, schema=schema, tools=detect_tools(ctx, "triage" if first else "draft"), ctx=ctx,
                          max_turns=TRIAGE_MAX_TURNS if name == "detect-triage" else DETECT_MAX_TURNS)

    run = {"id": None}

    def load_node(state):
        ctx.now, ctx.calls = state["now"], 0
        run["id"] = f"detect-{state['now']}-{uuid.uuid4().hex[:8]}"  # 이 실행의 초안들이 같이 갖는 기록 — 같은 실행의 내보내기를 지문에서 넘기는 근거 (hitl._own_exports)
        feed, rejected = detect_ops.drop_rejected(ctx, detect_ops.snapshot_feed(ctx))
        feed, applied = detect_ops.drop_applied(ctx, feed)
        sec = detect_ops.security_urls(ctx)  # 관문이 security로 남긴 소식 — 모델에게 표시해 주고, 「안 쓴다」며 버리면 코드가 되돌린다 (HYO-205)
        feed = [{**it, "security": True} if it.get("url") in sec else it for it in feed]
        slotted = detect_ops.slot_reasons(ctx)  # 자리 질의가 이미 후보로 고른 것 — 어느 repo의 어느 역할인지 넘기고, 거르기가 다시 빼지 못하게 한다 (HYO-205)
        feed = [{**it, "candidate_for": slotted[it["url"]]} if it.get("url") in slotted else it for it in feed]
        missing = detect_ops.missing_targets(ctx)  # 폴더가 없는 대상 repo — 오류로 세고(조용히 추측하지 않게) 그 repo 후보는 screen이 뺀다 (HYO-246)
        return {"stack": detect_ops.read_stack(ctx), "feed": feed, "rejected": rejected + applied, "missing": [m["repo"] for m in missing],
                "p3": [{"kind": "target_missing", "repo": m["repo"], "path": m["path"]} for m in missing]}

    async def triage_node(state):
        feed, rejected = state["feed"], state.get("rejected") or []
        if not feed:  # 소식이 없으면 모델을 부르지 않는다 — 조용한 주가 정상이다 (R3)
            return {"kept": [], "skipped": [], "dropped": [], "excluded": rejected}
        try:
            r = await agent("detect-triage", TRIAGE_INSTRUCTIONS, Triage, triage_prompt(state["now"], state["stack"], feed))
        except Exception as e:  # 400·파싱 실패·recursion limit — 후보 없음으로 두고 오류로 센다 (R1: 추측으로 채우지 않는다)
            return {"kept": [], "skipped": [], "dropped": [], "excluded": rejected,
                    "p3": [{"kind": "model_error", "stage": "triage", "type": type(e).__name__, "message": str(e)[:300]}]}
        t = r.structured
        cands = [c.model_dump() for c in t.candidates]
        excluded, p3 = [e.model_dump() for e in t.excluded], []
        # 못 버리는 소식 (HYO-205, 사용자 2026-11-20) — 보안 소식은 「우리 코드에 없다」는 이유로 빼지 않는다(찾아본 결과를 적어 올린다) ·
        # 자리 질의가 고른 후보는 어디에 쓰나를 다시 판단해 빼지 않는다(실행마다 갈렸다). 버렸으면 코드가 되돌려 후보로 다시 쓰게 한다
        sec = {it["url"] for it in feed if it.get("security")}
        slotted = {it["url"] for it in feed if it.get("candidate_for")}
        # 거르기의 답을 코드가 맞춘다 (HYO-206 — W46을 세 번 돌리니 여기서 갈렸다): 같은 내용이라며 버린 소식은 그 후보의 근거에 붙이고,
        # 후보에도 버린 것에도 없는 소식은 다시 묻는다
        excluded, folded = detect_ops.fold_duplicates(cands, excluded)
        missing = detect_ops.unanswered(feed, cands, excluded)
        pairs = detect_ops.to_rescue(excluded, sec, slotted, missing)
        back, rescued = [e for e, _ in pairs], []
        if back:
            urls = [e["url"] for e in back]
            ask = {e["url"] for e, why in pairs if why == "missing"}  # 버려도 되는 것은 답을 빠뜨린 소식뿐이다 — 보안 · 자리 후보는 후보로만 돌아온다
            try:
                rr = await agent("detect-rescue", RESCUE_INSTRUCTIONS, Rescued,
                                 rescue_prompt(state["now"], state["stack"], [it for it in feed if it.get("url") in urls],
                                               [{**e, "kept_because": why} for e, why in pairs]))
                got = [c.model_dump() for c in rr.structured.candidates]
                for c in got:  # 버릴 때 적은 패턴을 후보로 옮긴다 — 초안에 찾아본 결과가 남는다
                    mine = [p for e in back if e["url"] in c["source_urls"] for p in e.get("checked") or []]
                    c["checked"] = list(dict.fromkeys(list(c.get("checked") or []) + mine))
                rescued = sorted({u for c in got for u in c["source_urls"] if u in urls})
                cands += got
                again = [e.model_dump() for e in rr.structured.excluded if e.url in ask and e.url not in rescued]
                excluded = [e for e in excluded if e["url"] not in rescued] + again
                excluded, more = detect_ops.fold_duplicates(cands, excluded)
                folded += more
            except Exception as e:  # 되돌리기에 실패하면 버린 채로 두되 오류로 센다 — 보고서의 security.excluded에 남는다
                p3.append({"kind": "model_error", "stage": "rescue", "type": type(e).__name__, "message": str(e)[:300]})
        silent = detect_ops.unanswered(feed, cands, excluded)  # 다시 물어도 답이 없는 소식 — 버린 것으로 적되 사람이 보게 따로 센다
        excluded += [{"url": u, "reason": "거르기도 되돌리기도 답하지 않았다 — 판정 없음", "why": "other", "unanswered": True} for u in silent]
        renamed = detect_ops.fix_names(cands, detect_ops.gate_tech(ctx))  # technology는 관문이 붙인 이름으로 (실행마다 Electron / Node.js로 갈렸다)
        # affected는 코드가 맞춘다 — 자리 후보는 자리 설정의 component로(HYO-219), 그 뒤 매핑에 있는 기술은 매핑 그대로(R16, HYO-246 — 의존성이면 매핑이 이긴다)
        slot_fixed = detect_ops.fix_slot_affected(cands, feed)
        r16 = detect_ops.fix_affected(cands, detect_ops.dependency_map(ctx))
        notes = {"folded": folded, "missing": missing, "unanswered": silent, "renamed": renamed, "slot_affected": slot_fixed, "r16": r16,
                 "missing_targets": state.get("missing") or []}
        kept, skipped, dropped = detect_ops.screen(ctx, cands, state["feed"], missing=state.get("missing") or ())
        excluded = detect_ops.check_unused(ctx, excluded)  # 「안 쓴다」는 말을 repo_grep 기록과 대조 (HYO-205)
        security = {"total": len(sec), "rescued": [u for u in rescued if u in sec],
                    "excluded": [{"url": e["url"], "reason": e["reason"]} for e in excluded if e["url"] in sec]} if sec else None
        if slotted:  # 자리 질의 후보의 결과 — 남은 excluded는 되돌리기에 실패한 것뿐이다
            security = {**(security or {}), "slot_candidates": {"total": len(slotted), "rescued": [u for u in rescued if u in slotted],
                                                              "excluded": [e["url"] for e in excluded if e["url"] in slotted]}}
        return {"kept": kept, "skipped": skipped, "dropped": dropped, "excluded": rejected + excluded, "security": security, "triage_notes": notes, "p3": p3}

    async def draft_all(state):
        """남은 후보마다 draft를 병렬로 — 결과는 후보 순서대로 합친다 (번호는 write가 idx 순서로 준다)."""
        outs = await asyncio.gather(*(draft_node({"now": state["now"], "w": w, "item": _item_for(state["feed"], w["candidate"]["source_urls"])})
                                      for w in state.get("kept") or []))
        return {"drafts": [d for o in outs for d in o.get("drafts") or []], "p3": [p for o in outs for p in o.get("p3") or []]}

    async def draft_node(inp):
        w = inp["w"]
        cand = w["candidate"]
        related = [{k: r[k] for k in ("id", "status", "technology", "use")} for r in w["related"]]
        try:
            r = await agent("detect-draft", DRAFT_INSTRUCTIONS, DraftBody,
                            draft_prompt(inp["now"], cand, inp["item"], related, detect_ops.usage_lines(ctx, cand.get("checked"))))
        except Exception as e:
            return {"p3": [{"kind": "model_error", "stage": "draft", "technology": cand["technology"], "source_urls": cand.get("source_urls") or [],
                            "type": type(e).__name__, "message": str(e)[:300]}]}
        return {"drafts": [{"w": w, "draft": r.structured.model_dump()}]}

    def write_node(state):
        written = []
        for d in sorted(state.get("drafts") or [], key=lambda x: x["w"]["idx"]):  # 병렬 가지의 도착 순서와 무관하게 소식 순서대로 번호를 받는다
            w, cand = d["w"], d["w"]["candidate"]
            body = detect_ops.compose(detect_ops.with_usage(ctx, d["draft"], cand), w)  # 후보의 정체·related · 코드 찾기 결과는 코드가 적는다 — draft는 서술만 한다
            ctx.calls += 1
            item_id = hitl.new_item_id(ctx, "detect", cand["technology"])  # 별도 파일은 초안 파일을 쓰는 순간에 생긴다 (설계 §3-1)
            sources = [it["id"] for it in state["feed"] if it.get("id") and it.get("url") in cand["source_urls"]]
            res = hitl.write_adr(ctx, item_id, "detect", AdrDraft.model_validate(body), lifecycle="REVIEW_CANDIDATE",
                                 final_lifecycle="REVIEW_CANDIDATE", by="graph", hold_export=True, run_id=run["id"], sources=sources)  # 내보내기를 켰으면 Inbox 검토까지 로컬에 둔다 (결정 8)
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
                r = await agent("detect-repair", REPAIR_INSTRUCTIONS, DraftBody,
                                repair_prompt(state["now"], x["candidate"], item, x["body"], x["validate_errors"]))
                body = r.structured.model_dump()
            except Exception as e:
                p3.append({"kind": "model_error", "stage": "repair", "adr": x["id"], "type": type(e).__name__, "message": str(e)[:300]})
                out.append(x)
                continue
            fixed = detect_ops.compose(detect_ops.with_usage(ctx, body, x["candidate"]), x["w"])
            errs = detect_ops.rewrite(ctx, x["path"], x["id"], AdrDraft.model_validate(fixed), item_id=x.get("item"))
            out.append({**x, "body": body, "validate_errors": errs, "repaired": True})
        return {"written": out, "p3": p3}

    def report_node(state):
        failed = [p for p in state.get("p3") or [] if p.get("stage") == "draft"]
        rep = detect_ops.detect_report(ctx, state["now"], skipped=state.get("skipped") or [], dropped=state.get("dropped") or [],
                                       excluded=state.get("excluded") or [], written=state.get("written") or [], failed=failed,
                                       security=state.get("security"), notes=state.get("triage_notes"))
        return {"report": rep}

    steps = {"load": load_node, "triage": triage_node, "draft": draft_all, "write": write_node, "repair": repair_node, "report": report_node}
    assert tuple(steps) == STEPS, (tuple(steps), STEPS)

    async def step(state, name):
        out = steps[name](state)
        return merge(state, await out if asyncio.iscoroutine(out) else out)

    async def flow(now):
        s = {"now": now, "drafts": [], "p3": []}
        for name in ("load", "triage", "draft", "write"):
            await step(s, name)
        if after_write(s) == "repair":
            await step(s, "repair")
        return await step(s, "report")

    flow.steps = steps
    return flow


async def detect(ctx, model, now, model_name):
    check_scope(ctx)
    runner = as_runner(model)
    log = runner.attach_log(ctx, "detect", model_name)
    s = await detect_flow(ctx, runner)(now)
    p3 = list(s.get("p3") or [])
    p3 += [{"kind": "draft_invalid", "adr": c["adr"], "errors": c["validate_errors"]}
           for c in s["report"]["candidates"] if c["validate_errors"]]
    return s["report"], {"p3_errors": p3, **log.totals()}

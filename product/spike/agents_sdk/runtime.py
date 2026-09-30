"""런타임 스파이크 1번 — OpenAI Agents SDK (HYO-63, 기준 docs/design/runtime-spike.md).

revisit:  코드가 순서를 쥔다 — radar.py list → 조건마다 Agent(읽기 도구 3개 + Verdict 구조화 출력) → decide --apply → run_report
capture:  1턴  Agent(match_adr · propose_adr[needs_approval]) → 멈춤 → RunState.to_json을 파일로
          2턴  (새 프로세스, resume.py) 파일 → RunState.from_json → approve()/reject() → Runner.run 재개

프롬프트·스키마·도구 본체·보고서는 common/ 한 벌이다. 여기는 Agents SDK의 형식(function_tool · Agent · Runner · RunState)으로 감싸기만 한다.
상태 파일에 컨텍스트를 넣지 않는다 (context_serializer → {}) — 환경변수·경로가 파일로 새지 않게. 복원 때 새 Ctx로 갈아 끼운다.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

from agents import Agent, RunConfig, RunContextWrapper, Runner, function_tool, set_tracing_disabled  # noqa: E402
from agents.run_state import RunState  # noqa: E402

from common import ops  # noqa: E402
from common.core import check_scope  # noqa: E402
from common.prompts import CAPTURE_INSTRUCTIONS, REVISIT_INSTRUCTIONS, capture_prompt, condition_prompt  # noqa: E402
from common.schemas import AdrDraft, CaptureResult, Verdict  # noqa: E402

RUN_CONFIG = dict(tracing_disabled=True, trace_include_sensitive_data=False)


# ── 도구: common/ops 본체를 function_tool로 ─────────────────────────

def _tool(fn, **kw):
    return function_tool(fn, name_override=fn.__name__.lstrip("_"), description_override=ops.DOCS[fn.__name__.lstrip("_")], **kw)


def _snapshot_fetch(ctx: RunContextWrapper, url: str) -> str:
    return ops.snapshot_fetch(ctx.context, url)


def _snapshot_search(ctx: RunContextWrapper, query: str) -> str:
    return ops.snapshot_search(ctx.context, query)


def _read_repo_file(ctx: RunContextWrapper, repo: str, path: str) -> str:
    return ops.read_repo_file(ctx.context, repo, path)


def _match_adr(ctx: RunContextWrapper, technology: str, repo: str, component: str, use: str) -> str:
    return ops.match_adr(ctx.context, technology, repo, component, use)


def _propose_adr(ctx: RunContextWrapper, draft: AdrDraft) -> str:
    return ops.propose_adr(ctx.context, draft)


REVISIT_TOOLS = [_tool(_snapshot_fetch), _tool(_snapshot_search), _tool(_read_repo_file)]
MATCH = _tool(_match_adr)
PROPOSE = _tool(_propose_adr, needs_approval=True)  # 사람 승인 전에는 실행되지 않는다
PROPOSE_UNGATED = _tool(_propose_adr)  # 음성 대조 전용 — 승인 없이 실행


def _usage(r):
    return [{"input_tokens": x.usage.input_tokens, "output_tokens": x.usage.output_tokens} for x in r.raw_responses]


# ── revisit ──────────────────────────────────────────────────────────

async def _judge(ctx, model, adr, cond, now, settings):
    """조건 하나 → decide 입력 한 줄 + (있으면) P3 오류 + 토큰."""
    kw = {"model_settings": settings} if settings is not None else {}
    agent = Agent(name="condition-judge", instructions=REVISIT_INSTRUCTIONS, tools=REVISIT_TOOLS, model=model,
                  output_type=Verdict, **kw)
    ctx.calls = 0
    try:
        r = await Runner.run(agent, condition_prompt(adr, cond, now), context=ctx, max_turns=ops.REVISIT_MAX_TURNS,
                             run_config=RunConfig(**RUN_CONFIG))
    except Exception as e:  # 400·파싱 실패·max_turns — 안전 실패 (R1)
        row, err = ops.error_row(adr, cond, e)
        return row, err, []
    err = None if ctx.calls else {"adr": adr["id"], "condition_id": cond.get("id"), "kind": "tool_call_missing"}
    return ops.verdict_row(adr, cond, r.final_output), err, _usage(r)


async def revisit(ctx, model, now, settings=None):
    set_tracing_disabled(True)  # P5 — 기본값은 트레이스를 OpenAI로 올린다
    check_scope(ctx)
    rows, errors, usage = [], [], []
    for adr in ops.radar(ctx, "list", ctx.adr_dir, "--in-scope"):
        for cond in adr.get("revisit_when") or []:
            if cond.get("check_type") == "human":
                rows.append(ops.human_row(adr, cond))
                continue
            row, err, u = await _judge(ctx, model, adr, cond, now, settings)
            rows.append(row)
            errors += [err] if err else []
            usage += u
    d = ops.decide(ctx, rows, now)
    return ops.revisit_report(ctx, d, now), {"p3_errors": errors, "model_calls": len(usage),
                                             "input_tokens": sum(x["input_tokens"] for x in usage),
                                             "output_tokens": sum(x["output_tokens"] for x in usage)}


# ── capture ──────────────────────────────────────────────────────────

def capture_agent(model, settings=None, gate=True):
    kw = {"model_settings": settings} if settings is not None else {}
    return Agent(name="capture", instructions=CAPTURE_INSTRUCTIONS, tools=[MATCH, PROPOSE if gate else PROPOSE_UNGATED],
                 model=model, output_type=CaptureResult, **kw)


def _args(item):
    a = getattr(item.raw_item, "arguments", None) or (item.raw_item.get("arguments") if isinstance(item.raw_item, dict) else None)
    try:
        return json.loads(a) if isinstance(a, str) else a
    except json.JSONDecodeError:
        return a


async def capture_turn1(ctx, model, conversation, now, state_path, settings=None, gate=True):
    set_tracing_disabled(True)
    ctx.now, ctx.calls = now, 0
    r = await Runner.run(capture_agent(model, settings, gate), capture_prompt(conversation, now), context=ctx,
                         max_turns=ops.CAPTURE_MAX_TURNS, run_config=RunConfig(**RUN_CONFIG))
    diag = {"usage": _usage(r), "p3_errors": [] if ops.matched(ctx) else [{"kind": "tool_call_missing", "message": "match_adr를 부르지 않음"}]}
    if r.interruptions:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(r.to_state().to_json(context_serializer=lambda _: {}), f, ensure_ascii=False)
        preview = [{"tool": i.tool_name, "arguments": _args(i)} for i in r.interruptions]
        return ops.capture_report(ctx, now, link_candidates=[], awaiting=True, preview=preview), diag
    out = r.final_output
    return ops.capture_report(ctx, now, link_candidates=list(out.link_candidates), awaiting=False, note=out.note), diag


async def capture_turn2(ctx, model, state_path, decision, now, settings=None):
    """새 프로세스에서 부른다. decision: approve | reject — 사람의 답을 여기로만 넣는다."""
    set_tracing_disabled(True)
    ctx.now, ctx.calls = now, 0
    agent = capture_agent(model, settings)
    state = await RunState.from_json(agent, json.load(open(state_path, encoding="utf-8")), context_override=RunContextWrapper(ctx))
    pending = state.get_interruptions()
    for item in pending:
        if decision == "approve":
            state.approve(item)
        else:
            state.reject(item, rejection_message="사용자가 이 초안을 거부했다. 저장하지 않는다.")
    r = await Runner.run(agent, state, max_turns=ops.CAPTURE_MAX_TURNS, run_config=RunConfig(**RUN_CONFIG))
    out = r.final_output
    rep = ops.capture_report(ctx, now, link_candidates=list(out.link_candidates) if out else [], awaiting=bool(r.interruptions),
                             note=out.note if out else None)
    return rep, {"usage": _usage(r), "pending_restored": len(pending)}


# ── eval 어댑터가 부르는 런타임 인터페이스 ────────────────────────────

class Runtime:
    name = "agents_sdk"
    real_model = "litellm"
    state_filename = "capture-state.json"

    def add_args(self, ap):
        ap.add_argument("--no-register", dest="register", action="store_false",
                        help="litellm 모델 표 등록을 끈다 — 어댑터 기본값 재현용. 기본은 등록 (사용자 결정 2026-09-30: 네이티브 구조화 출력)")

    def real_spec(self, a):
        return {"kind": "litellm", "register": a.register}

    def label(self, spec):
        return "litellm-registered" if spec["register"] else "litellm-default"

    def new_shapes(self, spec):
        if spec["kind"] == "stub":
            return None
        import models
        return models.RequestShapes()

    def make_model(self, spec, shapes, flow):
        if spec["kind"] == "stub":
            from stub_model import CaptureStub, StubModel
            return CaptureStub(spec["script"]) if flow == "capture" else StubModel(spec["script"])
        import models
        return models.make_litellm(spec["register"], shapes)

    def settings(self, spec):
        if spec["kind"] == "stub":
            return None
        import models
        return models.settings()

    revisit = staticmethod(revisit)
    capture_turn1 = staticmethod(capture_turn1)

    def resume_cmd(self, spec, state_path, decision, now, radar_py, out_dir):
        cmd = [sys.executable, os.path.join(HERE, "resume.py"), "--state", state_path, "--decision", decision, "--now", now,
               "--radar", radar_py, "--out", out_dir]
        if spec["kind"] == "stub":
            return cmd + ["--stub-script", spec["script_file"]]
        return cmd + ["--litellm"] + ([] if spec["register"] else ["--no-register"])

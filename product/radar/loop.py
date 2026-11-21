"""에이전트 루프 — 모델에 묻고, 도구 요청이 오면 실행해 결과를 붙이고, 도구 요청 없이 답하면 끝낸다 (ADR 0030, HYO-220).

처음(0009)엔 LangChain create_agent가 이 루프를 돌렸다. 루프 자체는 while 하나 크기라 직접 쓴다 — 모델 자리는 둘이다:
  models.AnthropicModel  Anthropic API (구조화 출력 output_config.format · effort · adaptive thinking)
  stub_model.StubChat    정해진 답 (스텁 eval · 데스크톱 스텁 모드)
구독 CLI 엔진(engines.ClaudeCliRunner)은 이 루프를 쓰지 않는다 — Claude Code가 루프를 돈다.

대화(Conversation)는 Anthropic Messages 형식의 dict 목록이라 그대로 JSON이 된다 — capture가 사람 승인에서 멈출 때
이 목록과 대기 중인 도구 호출을 SQLite에 저장하고, 새 프로세스가 읽어 이어 간다 (hitl.ThreadStore).

모델 자리의 약속: `call(Request) -> Reply` (동기) · `async acall(Request) -> Reply` (비동기) · `attach_log(ctx, flow, model_name) -> CallLog`.
Reply.content는 Anthropic 응답의 content 블록(dict) 목록 — text · tool_use · thinking(받은 그대로 되돌려 보낸다).
"""

import copy
import inspect
import json
from dataclasses import dataclass, field

from pydantic import ValidationError


class LoopError(RuntimeError):
    """루프 실패 — 턴 상한 · 구조화 출력 없음 · 스키마 불일치 · 모르는 도구. 노드가 model_error로 센다 (안전 실패, R1)."""


def limit(max_turns):
    """모델 호출 상한 — 도구를 부르는 턴 max_turns번 + 마지막 답 한 번. (LangChain 판의 recursion limit 2·n+2 단계와 같은 몫)"""
    return max_turns + 1


@dataclass
class Tool:
    """모델에게 주는 도구 하나. fn(env, **입력) → 결과 문자열. schema는 입력 JSON 스키마(Anthropic `input_schema`)."""
    name: str
    description: str
    schema: dict
    fn: object

    def spec(self):
        return {"name": self.name, "input_schema": self.schema, "description": self.description}


@dataclass
class ToolEnv:
    """도구가 받는 실행 정보 — ctx(Ctx) · conv(지금까지의 대화, 가드가 센다) · config(capture 재개가 넘기는 thread_id · 승인 정보)."""
    ctx: object
    conv: "Conversation"
    config: dict = field(default_factory=dict)


@dataclass
class Request:
    agent: str
    system: str
    messages: list
    tools: list      # Tool 목록
    schema: object   # 최종 답의 pydantic 모델


@dataclass
class Reply:
    content: list                 # Anthropic content 블록 dict
    stop_reason: str = "end_turn"


@dataclass
class Conversation:
    messages: list = field(default_factory=list)
    tool_calls: int = 0
    turns: int = 0

    @classmethod
    def start(cls, user):
        return cls(messages=[{"role": "user", "content": user}])

    def to_json(self):
        return {"messages": self.messages, "tool_calls": self.tool_calls, "turns": self.turns}

    @classmethod
    def from_json(cls, d):
        return cls(messages=list(d.get("messages") or []), tool_calls=int(d.get("tool_calls") or 0), turns=int(d.get("turns") or 0))

    def tool_names(self):
        """tool_use id → 도구 이름 (tool_result에는 이름이 없다)."""
        return {b["id"]: b["name"] for m in self.messages if m["role"] == "assistant" and isinstance(m["content"], list)
                for b in m["content"] if b.get("type") == "tool_use"}

    def results(self):
        """[(도구 이름, 결과 글)] — 받은 순서대로."""
        names, out = self.tool_names(), []
        for m in self.messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                out += [(names.get(b.get("tool_use_id")), text_of(b.get("content"))) for b in m["content"] if b.get("type") == "tool_result"]
        return out

    def prompts(self):
        """사람(호출자)이 준 글 — 처음 user 메시지들의 문자열."""
        return [m["content"] for m in self.messages if m["role"] == "user" and isinstance(m["content"], str)]


def text_of(content):
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content or [])


def tool_calls_of(reply):
    return [b for b in reply.content if b.get("type") == "tool_use"]


def final_answer(reply, schema):
    """도구 요청이 없는 답 → 스키마로 검증한 객체. 구조화 출력은 text 블록 하나에 JSON으로 온다."""
    text = "".join(b.get("text", "") for b in reply.content if b.get("type") == "text").strip()
    if not text:
        raise LoopError(f"구조화 출력 없음 (stop_reason={reply.stop_reason})")
    try:
        return schema.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise LoopError(f"구조화 출력이 스키마에 안 맞는다: {str(e)[:200]}") from e


def _result(call, content, error):
    return {"type": "tool_result", "tool_use_id": call["id"], "content": content, "is_error": error}


def run_tools(conv, calls, tools, ctx, config=None, decisions=None):
    """한 턴의 도구 호출들을 실행하고 결과를 user 메시지 **하나**로 붙인다.
    같은 턴의 호출은 모두 같은 대화(이 턴의 assistant 메시지까지)를 본다 — LangChain ToolNode가 병렬로 돌리던 것과 같은 뜻이다(가드의 예산 · 본 글).
    decisions: {tool_use id: ("approve" | "edit", 입력) | ("reject", 글)} — capture 재개에서 사람의 답."""
    by_name = {t.name: t for t in tools}
    env = ToolEnv(ctx=ctx, conv=conv, config=dict(config or {}))
    out = []
    for c in calls:
        kind, val = (decisions or {}).get(c["id"], ("approve", None))
        if kind == "reject":
            out.append(_result(c, val, True))
            continue
        tool = by_name.get(c["name"])
        if tool is None:
            raise LoopError(f"모르는 도구 {c['name']!r}")
        args = val if kind == "edit" else c["input"]
        try:
            inspect.signature(tool.fn).bind(env, **args)
        except TypeError as e:  # 입력 모양이 틀렸다 — 실행하지 않고 모델에게 오류로 돌려준다
            out.append(_result(c, f"입력 오류: {e}", True))
            continue
        out.append(_result(c, tool.fn(env, **args), False))
    conv.tool_calls += len(out)
    if out:
        conv.messages.append({"role": "user", "content": out})


def _after(conv, reply, *, tools, schema, ctx, config, interrupt_on, rewrite):
    """모델 답 하나를 처리한다 → ("final", 답) | ("interrupt", 대기 중인 호출들) | ("continue", None)."""
    calls = tool_calls_of(reply)
    if not calls:
        return "final", final_answer(reply, schema)
    content = copy.deepcopy(reply.content)
    if rewrite:  # 호출을 실행 전에 고친다 (capture의 repo 고정 — 사람이 처음 보는 초안부터 정한 repo)
        content = [rewrite(b) if b.get("type") == "tool_use" else b for b in content]
    conv.messages.append({"role": "assistant", "content": content})
    calls = [b for b in content if b.get("type") == "tool_use"]
    if any(c["name"] in interrupt_on for c in calls):  # 사람 승인 — 실행하지 않고 멈춘다 (R9: 승인 전 쓰기 0)
        return "interrupt", calls
    run_tools(conv, calls, tools, ctx, config)
    return "continue", None


def _check_turns(conv, max_turns, agent):
    if conv.turns >= limit(max_turns):
        raise LoopError(f"턴 상한 {max_turns}을 넘었다 ({agent})")


def run(model, *, name, system, conv, schema, tools, ctx, max_turns, config=None, interrupt_on=(), rewrite=None):
    """동기 루프 → (종류, 값, conv). 종류: final(값 = 검증한 답) · interrupt(값 = 대기 중인 tool_use 블록들)."""
    while True:
        _check_turns(conv, max_turns, name)
        reply = model.call(Request(agent=name, system=system, messages=conv.messages, tools=tools, schema=schema))
        conv.turns += 1
        kind, val = _after(conv, reply, tools=tools, schema=schema, ctx=ctx, config=config, interrupt_on=interrupt_on, rewrite=rewrite)
        if kind != "continue":
            return kind, val, conv


async def arun(model, *, name, system, conv, schema, tools, ctx, max_turns, config=None, interrupt_on=(), rewrite=None):
    """비동기 루프 — run과 같다. 도구는 짧은 하위 프로세스(radar.py)라 루프 스레드에서 바로 돈다(병렬 가지는 모델 대기 동안 겹친다)."""
    while True:
        _check_turns(conv, max_turns, name)
        reply = await model.acall(Request(agent=name, system=system, messages=conv.messages, tools=tools, schema=schema))
        conv.turns += 1
        kind, val = _after(conv, reply, tools=tools, schema=schema, ctx=ctx, config=config, interrupt_on=interrupt_on, rewrite=rewrite)
        if kind != "continue":
            return kind, val, conv

"""LangChain 도구 — common/ops 본체를 @tool로 감싸기만 한다 (스파이크 langgraph_spike/runtime.py에서 옮겨 옴).

도구는 ToolRuntime[Ctx]로 실행 컨텍스트를 받는다 — 런타임 컨텍스트는 체크포인트에 저장되지 않는다.
모델에게 주는 도구에 status를 바꾸는 것은 없다 (R8): 쓰기는 propose_adr(승인 뒤 proposed 초안) 하나뿐이고,
detect는 이 도구도 모델에게 주지 않는다 — 초안 파일은 그래프의 write 노드(코드)가 쓴다.

revisit 판정·capture의 열기·검색은 가드를 씌운 판(guarded)을 준다 (HYO-137): 이 에이전트가 본 글(조건·대화·도구 결과)에 나온 URL만 열고,
열기·검색을 합쳐 LOOKUP_BUDGET회를 넘기면 도구가 거절한다. 403·없음에서 질의만 바꿔 다시 찾거나 URL을 지어내다
recursion limit에 걸리던 것(P3)을 프롬프트가 아니라 도구에서 끊는다 (규율 1). 판정마다 상태를 따로 두지 않고 에이전트 state의 메시지로 센다.
"""

import json
import re

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import HumanMessage, ToolMessage

from common import ops
from common.core import Ctx
from common.schemas import AdrDraft

from . import hitl


@tool("snapshot_fetch", description=ops.DOCS["snapshot_fetch"])
def snapshot_fetch(url: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.snapshot_fetch(runtime.context, url)


@tool("snapshot_search", description=ops.DOCS["snapshot_search"])
def snapshot_search(query: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.snapshot_search(runtime.context, query)


@tool("corpus_grep", description=ops.DOCS["corpus_grep"])
def corpus_grep(pattern: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.corpus_grep(runtime.context, pattern)


@tool("repo_grep", description=ops.DOCS["repo_grep"])
def repo_grep(pattern: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.repo_grep(runtime.context, pattern)


LOOKUP_BUDGET = 4 # source_hint 열기 1 + 검색 1 + 검색 결과 열기 2. 스킬은 403 케이스 실행 전체가 도구 6~7회다
LOOKUPS = ("snapshot_fetch", "snapshot_search")
_SCHEME = re.compile(r"https?://")


def _url_key(url):
    """radar.py snapshot fetch와 같은 정규화 (pipeline.detect_feed.url_key) — scheme·끝 / 뺀 것."""
    return _SCHEME.sub("", url or "", count=1).rstrip("/")


def _text(m):
    c = m.content
    return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)


def _seen(state):
    """이 에이전트가 지금까지 본 글 — 사람 메시지(조건·대화)와 도구 결과. scheme은 뺀다."""
    msgs = (state or {}).get("messages") or []
    return _SCHEME.sub("", "\n".join(_text(m) for m in msgs if isinstance(m, (HumanMessage, ToolMessage))))


def _lookups(state):
    return sum(1 for m in (state or {}).get("messages") or [] if isinstance(m, ToolMessage) and m.name in LOOKUPS)


def check_lookup(seen, lookups, url=None):
    """가드 본체 — seen: 지금까지 본 글(scheme 뺀 것) · lookups: 지금까지의 열기·검색 수(거절 포함).
    LangChain 도구(아래 lookup_guard — 에이전트 메시지에서 센다)와 구독 CLI 엔진의 MCP 서버(mcp_tools — 서버가 센다)가 같이 쓴다."""
    if lookups >= LOOKUP_BUDGET:
        return {"ok": False, "status": "lookup_budget_exhausted",
                "error": f"열기·검색 {LOOKUP_BUDGET}회를 다 썼다. 확인하지 못했으면 result=undetermined로 답하고 reason에 무엇이 실패했는지 적는다"}
    if url is not None and (not _url_key(url) or _url_key(url) not in seen):
        return {"ok": False, "status": "not_offered",
                "error": "보인 적 없는 URL이다 — 조건의 source_hint·대화·snapshot_search 결과에 나온 URL만 연다. URL을 지어내지 않는다"}
    return None


def lookup_guard(state, url=None):
    """거절 사유 dict · 통과면 None. 거절도 도구 결과로 남으니 다음 호출의 예산에 들어간다."""
    return check_lookup(_seen(state), _lookups(state), url)


GUARDED_DOCS = {  # 가드 판 설명 — 두 엔진이 같은 글자로 보인다
    "snapshot_fetch": ops.DOCS["snapshot_fetch"] + f" 조건·대화·검색 결과에 나온 URL만 열 수 있다. 열기·검색은 합쳐 {LOOKUP_BUDGET}회까지.",
    "snapshot_search": ops.DOCS["snapshot_search"] + f" 열기·검색은 합쳐 {LOOKUP_BUDGET}회까지.",
}


@tool("snapshot_fetch", description=GUARDED_DOCS["snapshot_fetch"])
def guarded_fetch(url: str, runtime: ToolRuntime[Ctx]) -> str:
    refused = lookup_guard(runtime.state, url)
    return json.dumps(refused, ensure_ascii=False) if refused else ops.snapshot_fetch(runtime.context, url)


@tool("snapshot_search", description=GUARDED_DOCS["snapshot_search"])
def guarded_search(query: str, runtime: ToolRuntime[Ctx]) -> str:
    refused = lookup_guard(runtime.state)
    return json.dumps(refused, ensure_ascii=False) if refused else ops.snapshot_search(runtime.context, query)


@tool("read_repo_file", description=ops.DOCS["read_repo_file"])
def read_repo_file(repo: str, path: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.read_repo_file(runtime.context, repo, path)


@tool("match_adr", description=ops.DOCS["match_adr"])
def match_adr(technology: str, repo: str, component: str, use: str, runtime: ToolRuntime[Ctx]) -> str:
    return ops.match_adr(runtime.context, technology, repo, component, use)


@tool("propose_adr", description=ops.DOCS["propose_adr"])
def propose_adr(draft: AdrDraft, runtime: ToolRuntime[Ctx]) -> str:
    """승인(approve·edit) 뒤에만 실행된다 — 번호 → 별도 파일 의도 → ADR 파일 + validate → 완료 (write-ahead, hitl.write_adr).
    thread_id가 item-id다. 승인 정보(결정·지문·edit 전 초안 해시)는 재개 호출이 config로 넘긴다."""
    d = draft if isinstance(draft, AdrDraft) else AdrDraft.model_validate(draft)
    c = runtime.context
    c.calls += 1
    cfg = (runtime.config or {}).get("configurable") or {}
    res = hitl.write_adr(c, cfg["thread_id"], "capture", d, approval=cfg.get("radar_approval"))
    return json.dumps(res, ensure_ascii=False)


REVISIT_TOOLS = [guarded_fetch, guarded_search, read_repo_file]
CAPTURE_TOOLS = [guarded_fetch, match_adr, propose_adr]  # 대화에 인용된 1차 출처를 열어 R17 basis를 확인한다 — 스킬과 같은 조건 (HYO-137, JHS-033)
DETECT_TOOLS = [snapshot_fetch, snapshot_search, repo_grep]  # 읽기만. repo_grep = 우리 코드가 그것을 쓰는지 (HYO-205)


def detect_tools(ctx, stage="triage"):
    """코퍼스(RADAR_CORPUS)가 켜져 있으면 grep을 더 준다 (DCI, HYO-201) — 꺼져 있으면 예전 그대로.
    repo_grep은 거르기(triage · rescue)에만 준다 — 초안 단계(draft · repair)는 거르기가 찾아본 결과(code_search)를 받는다.
    W47에서 초안 에이전트가 초안마다 같은 것을 다시 찾아 초안 단계 도구가 9~10회 → 22~36회였다 (HYO-205)."""
    base = DETECT_TOOLS if stage == "triage" else [t for t in DETECT_TOOLS if t is not repo_grep]
    return base + [corpus_grep] if ctx.env.get("RADAR_CORPUS") else base

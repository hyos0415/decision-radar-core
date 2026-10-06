"""LangChain 도구 — common/ops 본체를 @tool로 감싸기만 한다 (스파이크 langgraph_spike/runtime.py에서 옮겨 옴).

도구는 ToolRuntime[Ctx]로 실행 컨텍스트를 받는다 — 런타임 컨텍스트는 체크포인트에 저장되지 않는다.
모델에게 주는 도구에 status를 바꾸는 것은 없다 (R8): 쓰기는 propose_adr(승인 뒤 proposed 초안) 하나뿐이고,
detect는 이 도구도 모델에게 주지 않는다 — 초안 파일은 그래프의 write 노드(코드)가 쓴다.
"""

import json

from langchain.tools import ToolRuntime, tool

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


REVISIT_TOOLS = [snapshot_fetch, snapshot_search, read_repo_file]
DETECT_TOOLS = [snapshot_fetch, snapshot_search]  # 읽기만

"""모델에게 주는 도구 — common/ops 본체를 loop.Tool로 감싸기만 한다 (처음엔 LangChain @tool이었다 — ADR 0030, HYO-220).

도구는 ToolEnv로 실행 정보(ctx · 지금까지의 대화 · capture 재개의 config)를 받는다.
모델에게 주는 도구에 status를 바꾸는 것은 없다 (R8): 쓰기는 propose_adr(승인 뒤 proposed 초안) 하나뿐이고,
detect는 이 도구도 모델에게 주지 않는다 — 초안 파일은 흐름의 write 단계(코드)가 쓴다.

revisit 판정·capture의 열기·검색은 가드를 씌운 판(guarded)을 준다 (HYO-137): 이 에이전트가 본 글(조건·대화·도구 결과)에 나온 URL만 열고,
열기·검색을 합쳐 LOOKUP_BUDGET회를 넘기면 도구가 거절한다. 403·없음에서 질의만 바꿔 다시 찾거나 URL을 지어내다
턴 상한에 걸리던 것(P3)을 프롬프트가 아니라 도구에서 끊는다 (규율 1). 판정마다 상태를 따로 두지 않고 대화(conv)에서 센다.

입력 스키마는 LangChain이 보내던 것과 같은 모양이다(속성 · required · type, 제목 없음). propose_adr만 중첩 안의 제목 몇 개가 빠졌다.
"""

import copy
import json
import re

from common import ops
from common.schemas import AdrDraft

from . import hitl
from .loop import Tool


def params(*names):
    """문자열 입력 칸들 → 입력 JSON 스키마 (모두 필수)."""
    return {"properties": {n: {"type": "string"} for n in names}, "required": list(names), "type": "object"}


def model_params(**fields):
    """pydantic 모델 입력 칸 → $ref를 풀어 넣고 제목(title 키워드)을 뺀 입력 스키마. 속성 이름이 title인 칸은 그대로 둔다."""
    props, defs = {}, {}
    for name, model in fields.items():
        s = model.model_json_schema()
        defs.update(s.pop("$defs", {}))
        props[name] = s

    def walk(x, in_props=False):
        if isinstance(x, dict):
            if "$ref" in x:
                return walk(copy.deepcopy(defs[x["$ref"].split("/")[-1]]))
            return {k: walk(v, k == "properties") for k, v in x.items()
                    if in_props or not (k == "title" and isinstance(v, str))}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x
    return {"properties": walk(props, True), "required": list(fields), "type": "object"}


snapshot_fetch = Tool("snapshot_fetch", ops.DOCS["snapshot_fetch"], params("url"), lambda env, url: ops.snapshot_fetch(env.ctx, url))
snapshot_search = Tool("snapshot_search", ops.DOCS["snapshot_search"], params("query"), lambda env, query: ops.snapshot_search(env.ctx, query))
corpus_grep = Tool("corpus_grep", ops.DOCS["corpus_grep"], params("pattern"), lambda env, pattern: ops.corpus_grep(env.ctx, pattern))
repo_grep = Tool("repo_grep", ops.DOCS["repo_grep"], params("pattern"), lambda env, pattern: ops.repo_grep(env.ctx, pattern))


LOOKUP_BUDGET = 4 # source_hint 열기 1 + 검색 1 + 검색 결과 열기 2. 스킬은 403 케이스 실행 전체가 도구 6~7회다
LOOKUPS = ("snapshot_fetch", "snapshot_search")
_SCHEME = re.compile(r"https?://")


def _url_key(url):
    """radar.py snapshot fetch와 같은 정규화 (pipeline.detect_feed.url_key) — scheme·끝 / 뺀 것."""
    return _SCHEME.sub("", url or "", count=1).rstrip("/")


def _seen(conv):
    """이 에이전트가 지금까지 본 글 — 호출자가 준 글(조건·대화)과 도구 결과. 모델이 스스로 쓴 글은 넣지 않는다. scheme은 뺀다."""
    if conv is None:
        return ""
    return _SCHEME.sub("", "\n".join(conv.prompts() + [text for _, text in conv.results()]))


def _lookups(conv):
    return 0 if conv is None else sum(1 for name, _ in conv.results() if name in LOOKUPS)


def check_lookup(seen, lookups, url=None):
    """가드 본체 — seen: 지금까지 본 글(scheme 뺀 것) · lookups: 지금까지의 열기·검색 수(거절 포함).
    API 루프의 도구(아래 lookup_guard — 대화에서 센다)와 구독 CLI 엔진의 MCP 서버(mcp_tools — 서버가 센다)가 같이 쓴다."""
    if lookups >= LOOKUP_BUDGET:
        return {"ok": False, "status": "lookup_budget_exhausted",
                "error": f"열기·검색 {LOOKUP_BUDGET}회를 다 썼다. 확인하지 못했으면 result=undetermined로 답하고 reason에 무엇이 실패했는지 적는다"}
    if url is not None and (not _url_key(url) or _url_key(url) not in seen):
        return {"ok": False, "status": "not_offered",
                "error": "보인 적 없는 URL이다 — 조건의 source_hint·대화·snapshot_search 결과에 나온 URL만 연다. URL을 지어내지 않는다"}
    return None


def lookup_guard(conv, url=None):
    """거절 사유 dict · 통과면 None. 거절도 도구 결과로 남으니 다음 호출의 예산에 들어간다."""
    return check_lookup(_seen(conv), _lookups(conv), url)


GUARDED_DOCS = {  # 가드 판 설명 — 두 엔진이 같은 글자로 보인다
    "snapshot_fetch": ops.DOCS["snapshot_fetch"] + f" 조건·대화·검색 결과에 나온 URL만 열 수 있다. 열기·검색은 합쳐 {LOOKUP_BUDGET}회까지.",
    "snapshot_search": ops.DOCS["snapshot_search"] + f" 열기·검색은 합쳐 {LOOKUP_BUDGET}회까지.",
}


def _guarded_fetch(env, url):
    refused = lookup_guard(env.conv, url)
    return json.dumps(refused, ensure_ascii=False) if refused else ops.snapshot_fetch(env.ctx, url)


def _guarded_search(env, query):
    refused = lookup_guard(env.conv)
    return json.dumps(refused, ensure_ascii=False) if refused else ops.snapshot_search(env.ctx, query)


guarded_fetch = Tool("snapshot_fetch", GUARDED_DOCS["snapshot_fetch"], params("url"), _guarded_fetch)
guarded_search = Tool("snapshot_search", GUARDED_DOCS["snapshot_search"], params("query"), _guarded_search)
read_repo_file = Tool("read_repo_file", ops.DOCS["read_repo_file"], params("repo", "path"),
                      lambda env, repo, path: ops.read_repo_file(env.ctx, repo, path))
match_adr = Tool("match_adr", ops.DOCS["match_adr"], params("technology", "repo", "component", "use"),
                 lambda env, technology, repo, component, use: ops.match_adr(env.ctx, technology, repo, component, use))


def _propose_adr(env, draft):
    """승인(approve·edit) 뒤에만 실행된다 — 번호 → 별도 파일 의도 → ADR 파일 + validate → 완료 (write-ahead, hitl.write_adr).
    thread_id가 item-id다. 승인 정보(결정·지문·edit 전 초안 해시)는 재개 호출이 config로 넘긴다."""
    d = draft if isinstance(draft, AdrDraft) else AdrDraft.model_validate(draft)
    c = env.ctx
    c.calls += 1
    res = hitl.write_adr(c, env.config["thread_id"], "capture", d, approval=env.config.get("radar_approval"))
    return json.dumps(res, ensure_ascii=False)


propose_adr = Tool("propose_adr", ops.DOCS["propose_adr"], model_params(draft=AdrDraft), _propose_adr)


REVISIT_TOOLS = [guarded_fetch, guarded_search, read_repo_file]
CAPTURE_TOOLS = [guarded_fetch, match_adr, propose_adr]  # 대화에 인용된 1차 출처를 열어 R17 basis를 확인한다 — 스킬과 같은 조건 (HYO-137, JHS-033)
DETECT_TOOLS = [snapshot_fetch, snapshot_search, repo_grep]  # 읽기만. repo_grep = 우리 코드가 그것을 쓰는지 (HYO-205)


def detect_tools(ctx, stage="triage"):
    """코퍼스(RADAR_CORPUS)가 켜져 있으면 grep을 더 준다 (DCI, HYO-201) — 꺼져 있으면 예전 그대로.
    repo_grep은 거르기(triage · rescue)에만 준다 — 초안 단계(draft · repair)는 거르기가 찾아본 결과(code_search)를 받는다.
    W47에서 초안 에이전트가 초안마다 같은 것을 다시 찾아 초안 단계 도구가 9~10회 → 22~36회였다 (HYO-205)."""
    base = DETECT_TOOLS if stage == "triage" else [t for t in DETECT_TOOLS if t is not repo_grep]
    return base + [corpus_grep] if ctx.env.get("RADAR_CORPUS") else base

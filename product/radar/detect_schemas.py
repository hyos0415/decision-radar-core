"""detect 단계별 출력 스키마. 초안(AdrDraft)은 common/schemas — status 칸이 없다 (R8)."""

from typing import Literal

from pydantic import BaseModel, Field

from common.schemas import Affected, Evidence, RevisitCondition


class Candidate(BaseModel):
    # 스킬은 references/schema.md의 ADR 예시(`technology: LEANN`)로 이 칸의 모양을 배운다. 제품 모델은 그 예시를 안 읽어서
    # 첫 실제 실행(2026-10-06)에서 6/7이 「vllm 0.9.0 (scheduler rewrite …)」처럼 써 동일성 비교(R14 match·채점)가 깨졌다
    technology: str = Field(description="ADR의 technology 칸 = 동일성 키. 기술 이름만 쓴다 — 버전·설명·괄호를 붙이지 않는다 (예: LEANN, vLLM, pydantic)")
    dependency: bool = Field(description="대상 repo의 의존성(manifest)에 있는 패키지의 릴리스면 true. 새 모델·논문·서비스면 false")
    affected: list[Affected] = Field(description="실재하는 repo·component. 의존성이면 CLAUDE.md의 「의존성 → component」 매핑과 정확히 같게 (R16). 의존성 밖이면 하나 이상 (R10)")
    source_urls: list[str] = Field(description="이 후보의 근거가 된 소식의 URL (소식 목록의 url 그대로)")
    checked: list[str] = Field(default_factory=list, description="이 후보 때문에 repo_grep에 넣은 패턴을 넣은 글자 그대로 (찾아본 것이 없으면 빈 목록). 결과는 코드가 기록에서 읽어 초안에 적는다")


class Excluded(BaseModel):
    url: str
    reason: str
    # 「안 쓴다」는 말을 코드가 repo_grep 기록과 대조한다 (HYO-205) — W47을 세 번 돌리니 코드를 안 열고 「미사용」이라 적었고 판정이 갈렸다
    unused_claim: bool = Field(default=False, description="버린 이유가 「우리 코드가 그 모듈 · 함수 · 기능을 쓰지 않는다」에 기대면 true. 그러면 checked를 채운다")
    checked: list[str] = Field(default_factory=list, description="unused_claim이 true일 때 — 이번에 repo_grep에 넣어 0곳이 나온 패턴을 넣은 글자 그대로")
    # 버린 이유의 종류 — 이유 글은 실행마다 말이 달라 코드가 읽을 수 없다 (HYO-206, W46 세 번: 보안 소식을 「어느 manifest에도 없다」며 한 번 뺐고,
    # 같은 내용이라며 뺀 소식은 어느 후보에 붙었는지 남지 않았다)
    why: Literal["not_ours", "routine", "duplicate", "unclear", "other"] = Field(
        default="other", description="버린 이유의 종류. not_ours = 우리 스택 · 코드에 없거나 쓰지 않는다 · routine = breaking · security 등이 없는 일상 릴리스 · "
                                     "duplicate = 후보로 올린 다른 소식과 같은 내용 (duplicate_of를 채운다) · unclear = 무엇에 해당하는지 알 수 없는 글 · other = 그 밖")
    duplicate_of: str = Field(default="", description="why가 duplicate일 때 — 같은 내용이라 본 **후보의 소식 url** (candidates의 source_urls에 있는 것 그대로). 코드가 이 소식을 그 후보의 근거에 붙인다")


class Rescued(BaseModel):
    """코드가 되돌린 소식을 다시 쓴 것 — 못 버리는 소식(보안 · 자리 후보)은 후보로, 거르기가 답을 빠뜨린 소식은 후보나 excluded로 (HYO-205 · HYO-206)."""
    candidates: list[Candidate]
    excluded: list[Excluded] = Field(default_factory=list, description="kept_because가 missing인 소식만 — 거르기 규칙대로 버릴 것이면 여기에 이유와 함께. security · slot은 여기에 적지 않는다")


class Triage(BaseModel):
    """거르기 결과. 남긴 것과 버린 것을 모두 적는다 — 버린 이유가 진단 기록이 된다."""
    candidates: list[Candidate]
    excluded: list[Excluded]


class DraftBody(BaseModel):
    """초안의 서술 부분. technology·affected·related는 칸이 없다 — 코드가 후보(triage)와 radar.py match 결과로 채워 AdrDraft를 만든다.
    status 칸도 없다 (R8) — ops.render가 proposed로 박는다."""
    title: str
    use: str = Field(description="이 결정이 다루는 용도 — 한 구절")
    evidence: list[Evidence]
    context: str
    decision: str
    research_note: list[str]
    revisit_when: list[RevisitCondition] = Field(description="R17 — 소식에 적힌 판단 근거가 바뀌는 사건. 근거가 없으면 빈 목록 + research_note에 「재검토 조건 없음: …」")

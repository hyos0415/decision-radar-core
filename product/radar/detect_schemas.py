"""detect 단계별 출력 스키마. 초안(AdrDraft)은 common/schemas — status 칸이 없다 (R8)."""

from pydantic import BaseModel, Field

from common.schemas import Affected, Evidence, RevisitCondition


class Candidate(BaseModel):
    technology: str
    dependency: bool = Field(description="대상 repo의 의존성(manifest)에 있는 패키지의 릴리스면 true. 새 모델·논문·서비스면 false")
    affected: list[Affected] = Field(description="실재하는 repo·component. 의존성이면 CLAUDE.md의 「의존성 → component」 매핑과 정확히 같게 (R16). 의존성 밖이면 하나 이상 (R10)")
    source_urls: list[str] = Field(description="이 후보의 근거가 된 소식의 URL (소식 목록의 url 그대로)")


class Excluded(BaseModel):
    url: str
    reason: str


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

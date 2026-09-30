"""모델 출력·도구 입력 스키마. 두 런타임이 같은 스키마로 구조화 출력을 받는다."""

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    url: str
    source_type: Literal["official_doc", "release_note", "repo", "paper", "issue_pr", "community"]


class Verdict(BaseModel):
    """revisit 조건 하나의 판정."""
    result: Literal["met", "not_met", "undetermined"] | None
    reason: str | None
    observed_value: float | None
    evidence: list[Evidence]


class Affected(BaseModel):
    repo: str
    component: str


class AdrDraft(BaseModel):
    """capture 초안. status 칸이 없다 — 코드가 proposed로 박는다 (R8)."""
    title: str
    technology: str
    use: str
    affected: list[Affected]
    evidence: list[Evidence]
    context: str
    decision: str
    related: list[str]
    research_note: list[str]


class CaptureResult(BaseModel):
    # 정의는 스킬 references/schema.md의 run_report 표와 같다 — 스킬 모델이 읽는 정의를 제품 모델도 받는다 (2026-09-30 실측 뒤 추가)
    link_candidates: list[str] = Field(description="match_adr의 exact(3중 키 일치, R7)에 나온 ADR ID만. related나 방금 쓴 ADR은 넣지 않는다. exact가 없었으면 빈 목록")
    note: str

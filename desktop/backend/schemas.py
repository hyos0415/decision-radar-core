"""백엔드 API 계약 — 요청·응답 pydantic 모델 (HYO-91). FastAPI가 이것으로 `/openapi.json`을 만들고, 화면은 거기서 TS 타입을 생성한다.

  python desktop/backend/export_openapi.py            # desktop/openapi.json 다시 쓰기
  cd desktop && npm run gen:api                        # openapi.json → src/renderer/src/lib/api.gen.ts (openapi-typescript)

규칙
- 응답 모델은 서비스가 실제로 돌려주는 모양 그대로다. 어긋나면 FastAPI가 500(응답 검사 실패)을 낸다 — test_backend가 모든 엔드포인트를 지나므로 거기서 잡힌다
- 초안은 코어 `AdrDraft`를 그대로 쓴다 (한 벌). status 칸이 없다 (R8)
- **edit 요청의 draft만 dict로 받는다** — AdrDraft로 받으면 모르는 칸(`status` 등)이 조용히 버려져서 「status 칸 거부」(R8, `hitl.check_edit`)가 무력해진다
- 칸이 늘면 여기 → export_openapi → gen:api 순서. CI가 다시 뽑아 차이가 있으면 실패한다
"""

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from common.schemas import AdrDraft, Affected  # noqa: F401 — 계약에 그대로 쓴다

Action = Literal["approve", "edit", "reject", "ack"]
StaleAnswer = Literal["continue", "rejudge", "discard"]
Mode = Literal["stub", "demo"]
ItemState = Literal["awaiting_approval", "stale", "review", "pending", "blocked",  # capture · detect
                    "queued", "running", "error", "linked", "no_draft",  # 접수된 capture (화면 설계 ①)
                    "met", "not_met", "undetermined"]  # revisit 충족 알림 (화면 설계 ③)
Flow = Literal["detect", "revisit"]


# ── 공통 ──────────────────────────────────────────────────────────

class ErrorBody(BaseModel):
    detail: str


class Ok(BaseModel):
    ok: bool


# ── health ────────────────────────────────────────────────────────

class DemoInfo(BaseModel):
    adr_source: str = Field(description="원본 ADR repo (읽기만)")
    adr_copy: str = Field(description="쓰는 사본")
    snapshot: str = Field(description="detect 입력 폴더 (pipeline.detect_feed 출력)")
    funnel: Optional[str] = Field(description="건수 한 줄: 수집 → 레인 A → 관문 남김 → detect 입력")


class Health(BaseModel):
    ok: bool
    mode: Mode
    profile: str = Field(description="개발 데이터 프로필 (default · andbread)")
    model: str = Field(description="stub 또는 실제 모델 이름")
    flows: list[Flow] = Field(description="이 모드에서 돌릴 수 있는 흐름 — 데모는 detect만")
    data_dir: str
    adr_dir: str
    demo: Optional[DemoInfo] = None


# ── Inbox ─────────────────────────────────────────────────────────

class Usage(BaseModel):
    """이 작업의 모델 사용량 (HYO-98) — 호출마다 남는 llm_call 기록(product/radar/llm_log)의 합."""
    model: str = Field(description="이 작업에서 부른 모델 (여럿이면 쉼표로). 스텁은 stub")
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float] = Field(description="실제 비용 (USD). 단가를 모르는 모델이 섞이면 null — 0이 아니다. 스텁·로컬 모델은 0")
    estimated: bool = Field(description="스텁 — 토큰이 글자 수/4 추정이고 비용 0 (배관 확인용)")


class InboxItem(BaseModel):
    """Inbox 한 줄. kind: capture(승인 대기 초안) · detect(검토 대기 초안) · pending(접수된 capture — 처리 중이거나 초안 없이 끝남, 화면 설계 ①) ·
    revisit(재검토 조건 충족 알림, 화면 설계 ③). pending·revisit의 답은 「확인함」(ack)뿐 — Inbox에서 치울 뿐 ADR은 안 바꾼다 (R8)."""
    id: str
    kind: Literal["capture", "detect", "pending", "revisit"]
    title: str
    technology: Optional[str]
    affected: list[Affected]
    since: str
    waiting_days: int
    state: ItemState = Field(description="capture: awaiting_approval · stale / detect(별도 파일 export.state): review · pending · stale · blocked / "
                                         "pending: queued · running · error · linked · no_draft / revisit: met (판정 결과)")
    actions: list[Action] = Field(description="이 항목에 할 수 있는 답. 빈 목록이면 보기만")
    why: Optional[str] = Field(description="detect만: 왜 왔나 — 관문 판정 (예 `anthropic L5 × deprecation`). 관문 없이 만든 입력·capture는 null")
    adr: Optional[str] = Field(default=None, description="revisit: 조건이 충족된 ADR 번호 (「ADR 열기」 → /decisions/{adr})")
    message: Optional[str] = Field(default=None, description="pending: 작업 한 줄(오류·linked 이유) / revisit: 충족된 조건 문장")


class InboxList(BaseModel):
    items: list[InboxItem]


class InboxDetail(InboxItem):
    draft: Optional[AdrDraft] = Field(description="편집용 초안. detect는 초안 파일을 읽어 만든다 — 못 읽으면 null")
    markdown: str = Field(description="저장될(된) ADR 파일")
    changes: list[str] = Field(description="대기 중 바뀐 점 (지문 비교). 비면 그대로")
    number_note: str
    note: Optional[str] = Field(default=None, description="답할 수 없을 때만")
    usage: Optional[Usage] = Field(default=None, description="이 항목을 만드는 데 든 모델 사용량 (capture 1턴). detect 초안은 실행(GET /runs)에 있다")


class AnswerRequest(BaseModel):
    stale_answer: Optional[StaleAnswer] = Field(default=None, description="바뀐 점(stale)을 본 뒤의 답")


class EditRequest(BaseModel):
    draft: dict[str, Any] = Field(description="고친 초안 (AdrDraft 모양). 검사는 서버가 한다 — status·번호 칸이 있으면 edit_errors")
    stale_answer: Optional[StaleAnswer] = None


Outcome = Literal["written", "rejected", "stale", "edit_errors", "discarded", "rejudge", "blocked", "recovered", "acked", "error"]


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    outcome: Outcome
    message: Optional[str] = None
    adr: Optional[str] = None
    path: Optional[str] = None
    changes: Optional[list[str]] = Field(default=None, description="stale일 때 바뀐 점")
    answers: Optional[list[StaleAnswer]] = Field(default=None, description="stale일 때 고를 수 있는 답")
    errors: Optional[list[str]] = Field(default=None, description="edit_errors일 때")
    validate_errors: Optional[list[str]] = None
    new_item: Optional[str] = Field(default=None, description="rejudge로 새로 생긴 대기 항목")
    blocked: Optional[dict[str, Any]] = None
    usage: Optional[Usage] = Field(default=None, description="이 답에서 모델을 불렀으면 그 사용량 (capture 2턴). 모델을 안 부르면 null")


# ── capture ───────────────────────────────────────────────────────

class CaptureRequest(BaseModel):
    conversation: str = Field(description="조사 대화 원문 (20만 자까지)")


class CaptureResult(BaseModel):
    outcome: Literal["awaiting", "linked", "no_draft"]
    item: Optional[str] = Field(description="awaiting이면 Inbox 항목 id")
    message: str
    link_candidates: list[str] = Field(description="linked면 이미 있는 같은 결정")
    usage: Optional[Usage] = Field(default=None, description="capture 1턴(대화 읽기·초안)의 모델 사용량")


# ── Decisions (읽기만 — R8) ──────────────────────────────────────

class RevisitLast(BaseModel):
    result: Optional[str]
    checked_at: str
    reason: Optional[str]


class DecisionRevisit(BaseModel):
    id: Optional[str]
    check_type: Optional[str]
    condition: Optional[str]
    last: Optional[RevisitLast]


class RevisitSummary(BaseModel):
    met: int
    not_met: int
    undetermined: int
    unchecked: int


class Decision(BaseModel):
    id: str
    title: str
    file: str
    status: Optional[str]
    technology: Optional[str]
    use: Optional[str]
    date: Optional[str]
    affected: list[Affected]
    revisit: list[DecisionRevisit]
    revisit_summary: RevisitSummary
    last_checked: Optional[str]


class DecisionList(BaseModel):
    items: list[Decision]


class DecisionDetail(Decision):
    markdown: str


# ── 실행 ──────────────────────────────────────────────────────────

class RunDraft(BaseModel):
    adr: str
    title: str
    technology: Optional[str] = None
    validate_errors: list[str] = []


class RunAlert(BaseModel):
    adr: str
    condition: str
    result: str
    message: str


class RunExcluded(BaseModel):
    url: str
    title: Optional[str] = Field(default=None, description="detect 입력(feed.json)의 소식 제목. 입력에 없는 url이면 null")
    reason: str = Field(description="모델이 이 소식을 버린 이유 (detect 거르기)")
    why: Optional[str] = Field(default=None, description="관문이 이 소식을 남긴 이유 (gate.json, 예 `anthropic L5 × deprecation`). 수집 스냅숏 입력이 아니면 null")


class RunResult(BaseModel):
    id: str
    flow: Flow
    at: str
    mode: Mode
    trigger: str = Field(description="manual · schedule · job")
    ok: bool
    error: Optional[str]
    summary: list[str] = Field(description="요약 줄. 수집 스냅숏 입력이면 첫 줄이 건수")
    drafts: list[RunDraft]
    alerts: list[RunAlert]
    excluded: list[RunExcluded] = Field(default=[], description="detect가 버린 소식과 이유 — 초안 0건이어도 무엇을 보고 왜 버렸는지 남는다. revisit은 빈 목록")
    usage: Optional[Usage] = Field(default=None, description="이 실행의 모델 사용량. 실행이 죽어도 그때까지 부른 만큼 남는다")


class RunList(BaseModel):
    items: list[RunResult]


class ScheduleFlow(BaseModel):
    flow: Flow
    slot: str
    last_slot: str
    next_slot: str
    last_run: Optional[str]
    due: bool


class Schedule(BaseModel):
    enabled: bool
    flows: list[ScheduleFlow]


# ── 작업 + 폴링 (HYO-85) ─────────────────────────────────────────

JobKind = Literal["capture", "detect", "revisit"]


class JobRequest(BaseModel):
    conversation: Optional[str] = Field(default=None, description="capture만")


class Job(BaseModel):
    id: str
    kind: JobKind
    state: Literal["queued", "running", "done", "error"]
    created: str
    started: Optional[str]
    finished: Optional[str]
    message: Optional[str] = Field(description="한 줄 — 상태 창에 그대로")
    result: Union[CaptureResult, RunResult, None] = Field(description="끝났을 때: capture → CaptureResult, detect·revisit → RunResult")
    error: Optional[str]
    item: Optional[str] = Field(description="capture가 만든 Inbox 항목 (알림을 누르면 여기로)")
    mode: Mode
    preview: Optional[str] = Field(default=None, description="capture: 대화 첫 줄 (Inbox에 접수된 항목 제목)")
    acked: bool = Field(default=False, description="Inbox에서 「확인함」으로 치웠나")


class JobEnvelope(BaseModel):
    job: Job


class JobList(BaseModel):
    items: list[Job]


# ── 행동 로그 ─────────────────────────────────────────────────────

class EventRequest(BaseModel):
    event: str = Field(description="app_opened · item_opened · notification_shown · notification_clicked · capture_opened · decision_opened")
    item: Optional[str] = None
    detail: Optional[dict[str, Any]] = None

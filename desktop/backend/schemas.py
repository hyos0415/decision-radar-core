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

Action = Literal["approve", "edit", "reject", "ack", "reject_rest"]  # reject_rest = 자리 질의 「확인 필요」 나머지 전부 기각 (HYO-245)
StaleAnswer = Literal["continue", "rejudge", "discard"]
Mode = Literal["stub", "demo", "real"]
InboxKind = Literal["capture", "detect", "pending", "revisit", "check"]
ItemState = Literal["awaiting_approval", "stale", "review", "pending", "blocked",  # capture · detect
                    "queued", "running", "error", "linked", "no_draft",  # 접수된 capture (화면 설계 ①)
                    "met", "not_met", "undetermined",  # revisit 충족 알림 (화면 설계 ③)
                    "check"]  # 자리 질의 「확인 필요」 (HYO-245)
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


class RealInfo(BaseModel):
    """실사용 모드 (RADAR_APP_MODE=real, HYO-99) — 실제 ADR repo에 쓰고 승인은 main에 push한다."""
    adr_repo: str = Field(description="실제 ADR repo 작업 사본 (main)")
    snapshot_repo: str = Field(description="radar-snapshots 작업 사본 — 실행 전에 pull만 한다")
    snapshot: Optional[str] = Field(description="지금 detect 입력 폴더 (radar-snapshots/detect/<주차>). 아직 없으면 null")
    week: Optional[str] = Field(description="detect 입력 주차 (예 2026-W44)")
    claude_bin: Optional[str] = Field(description="구독 CLI 바이너리 (엔진이 claude-cli일 때)")
    api_key: bool = Field(description=".env에 ANTHROPIC_API_KEY가 있나 — capture는 API로 돈다. 값은 보이지 않는다")
    funnel: Optional[str] = Field(description="건수 한 줄: 수집 → 레인 A → 관문 남김 → detect 입력")
    shadow: bool = Field(default=False, description="API 그림자 — 주 엔진이 claude-cli면 같은 입력을 API로도 돌려 <data>/shadow.jsonl에 기록만 (화면에 안 띄운다, RADAR_SHADOW=off로 끔)")
    review: Literal["pr", "app"] = Field(default="app", description="detect 초안 검토 자리 (HYO-158) — pr: ADR repo PR(merge = 채택 · close = 기각, 앱 버튼도 PR을 merge·close) · app: 앱 Inbox에서만 (RADAR_REVIEW=app)")


class Health(BaseModel):
    ok: bool
    mode: Mode
    profile: str = Field(description="개발 데이터 프로필 (default · andbread)")
    model: str = Field(description="stub 또는 실제 모델 이름")
    flows: list[Flow] = Field(description="이 모드에서 돌릴 수 있는 흐름 — 데모는 detect만")
    engine: Optional[Literal["claude-cli", "api"]] = Field(default=None, description="detect·revisit 실행 엔진 — 스텁은 null, 데모는 api, 실사용은 RADAR_APP_ENGINE")
    data_dir: str
    adr_dir: str
    demo: Optional[DemoInfo] = None
    real: Optional[RealInfo] = None


# ── 엔진 (HYO-99 3단계) ────────────────────────────────────────────

class CliStatus(BaseModel):
    """구독 CLI(`claude`) 상태 — `claude --version` · `claude auth status --json`을 읽기만 한다. 이메일·조직은 담지 않는다."""
    installed: bool
    path: Optional[str] = Field(description="바이너리 경로 (RADAR_CLAUDE_BIN 또는 PATH)")
    version: Optional[str]
    logged_in: bool
    auth_method: Optional[str] = Field(description="claude.ai = 구독 로그인. 다른 값이면 `claude -p`가 API로 과금된다")
    subscription_type: Optional[str] = Field(description="pro · max · team 등 (CLI가 알려 준 그대로)")
    error: Optional[str] = Field(description="못 찾음 · 실행 실패 · 상태를 못 읽음. 없으면 null")


class EngineInfo(BaseModel):
    mode: Mode
    engine: Optional[Literal["claude-cli", "api"]] = Field(description="detect·revisit 실행 엔진 — 스텁은 null")
    ready: bool = Field(description="지금 detect·revisit을 돌릴 수 있나. claude-cli는 구독(claude.ai) 로그인일 때만")
    reason: Optional[str] = Field(description="준비 안 됨의 이유 (사람 말)")
    cli: CliStatus = Field(description="스텁에서도 읽는다 (읽기만, 사용량 0)")
    api_key: bool = Field(description=".env에 ANTHROPIC_API_KEY가 있나 (스텁은 늘 false — 보지 않는다)")
    capture_ready: bool = Field(description="capture를 돌릴 수 있나 — 실사용 capture는 API라 키가 필요하다. 스텁은 true")
    login_command: str = Field(description="터미널을 못 열 때 사람이 칠 명령")


class LoginResult(BaseModel):
    """로그인 창 열기 — 로그인은 터미널에서 사람이 마친다. 앱은 로그인 코드·토큰을 받지 않는다 (HYO-114)."""
    opened: bool = Field(description="OS 터미널을 열었나. false면 command를 직접 친다")
    command: str
    detail: str


# ── Inbox ─────────────────────────────────────────────────────────

class Usage(BaseModel):
    """이 작업의 모델 사용량 (HYO-98) — 호출마다 남는 llm_call 기록(product/radar/llm_log)의 합."""
    model: str = Field(description="이 작업에서 부른 모델 (여럿이면 쉼표로). 스텁은 stub")
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Optional[float] = Field(description="실제 비용 (USD). 단가를 모르는 모델이 섞이면 null — 0이 아니다. 스텁·로컬 모델은 0")
    estimated: bool = Field(description="스텁 — 토큰이 글자 수/4 추정이고 비용 0 (배관 확인용)")
    billing: Literal["stub", "api", "subscription", "mixed"] = Field(default="api", description="subscription = 구독 CLI(`claude -p`) — 과금이 없어 cost_usd가 null")
    cost_usd_equiv: Optional[float] = Field(default=None, description="구독 CLI가 알려 준 정가 환산 (USD) — 실제로 낸 돈이 아니다. 엔진 비교용")


class CardAction(BaseModel):
    """남김 카드의 행동 표시 (HYO-132) — 모델이 아니라 코드가 관문 kept_by의 변화 종류로 정한다."""
    kind: Literal["forced_migration", "optional_trial"] = Field(description="forced_migration(강제 이전: 모델 은퇴·EOL) · optional_trial(선택 실험: 후속 모델)")
    label: str = Field(description="배지 글자 (강제 이전 · 선택 실험)")
    reason: str = Field(description="한 줄 이유 (예 `anthropic · 모델 은퇴 공지 → 날짜부터 호출 실패`)")
    deadline: Optional[str] = Field(description="본문에서 뽑은 마감일 YYYY-MM-DD. 못 뽑았거나 날짜가 여럿이면 null (강제 이전만)")
    days_left: Optional[int] = Field(description="오늘부터 마감일까지 남은 일수 (D-day). 마감일이 없으면 null")
    deadline_quote: Optional[str] = Field(description="마감일을 뽑은 본문 줄 (근거)")
    deadline_note: Optional[str] = Field(description="마감일이 없을 때의 안내 (「마감일 확인 필요」). 있으면 null")


class InboxItem(BaseModel):
    """Inbox 한 줄. kind: capture(승인 대기 초안) · detect(검토 대기 초안) · pending(접수된 capture — 처리 중이거나 초안 없이 끝남, 화면 설계 ①) ·
    revisit(재검토 조건 충족 알림, 화면 설계 ③) · check(자리 질의 「확인 필요」 — 주당 한 항목, HYO-245). pending·revisit의 답은 「확인함」(ack)뿐 —
    Inbox에서 치울 뿐 ADR은 안 바꾼다 (R8). check는 상세의 후보마다 초안 만들기 · 기각, 항목에는 나머지 전부 기각(reject_rest)."""
    id: str
    kind: InboxKind
    title: str
    technology: Optional[str]
    affected: list[Affected]
    since: str = Field(description="처음 도착 시각 — 처리 중에 stale·보류로 바뀌어도 그대로다 (HYO-144)")
    waiting_days: int = Field(description="since부터 지난 날 수")
    state: ItemState = Field(description="capture: awaiting_approval · stale / detect(별도 파일 export.state): review · pending · stale · blocked / "
                                         "pending: queued · running · error · linked · no_draft / revisit: met (판정 결과) / check: check (확인 필요)")
    actions: list[Action] = Field(description="이 항목에 할 수 있는 답. 빈 목록이면 보기만")
    why: Optional[str] = Field(description="detect만: 왜 왔나 — 관문 판정 (예 `anthropic L5 × deprecation`). 관문 없이 만든 입력·capture는 null")
    action: Optional[CardAction] = Field(default=None, description="detect만: 남김 카드 표시 (HYO-132) — 관문 kept_by로 코드가 정한다 "
                                         "(모델 은퇴·EOL → 강제 이전 + 마감일 · 후속 모델 → 선택 실험). 그 밖은 null")
    adr: Optional[str] = Field(default=None, description="revisit: 조건이 충족된 ADR 번호 (「ADR 열기」 → /decisions/{adr})")
    answer_error: Optional[str] = Field(default=None, description="접수한 답을 ADR repo에 반영하지 못한 이유 (HYO-217) — 같은 답을 다시 하면 재시도한다 (중복 없음). 없으면 null")
    message: Optional[str] = Field(default=None, description="pending: 작업 한 줄(오류·linked 이유) / revisit: 충족된 조건 문장 / check: 후보 · 자리 수 한 줄")


class InboxList(BaseModel):
    items: list[InboxItem]


class CheckRow(BaseModel):
    """「확인 필요」 후보 한 줄 — 자리 질의가 적합도 1로 본 출시 하나 (같은 모델의 다른 글은 접혀 있다)."""
    id: str = Field(description="소식 id (코퍼스 항목 id) — 답할 때 row로 보낸다")
    title: str
    url: str
    source: str = Field(description="vendor · huggingface · hn · reddit …")
    model: str = Field(description="자리 질의가 붙인 모델 이름 (없으면 빈 문자열)")
    reason: str = Field(description="적합도 1의 이유 — 무엇이 모자라나")
    switch: str = Field(description="바꾸려면 필요한 것 (v2 now_via) — 없으면 빈 문자열")
    other_slots: list[str] = Field(description="이 후보가 함께 든 다른 자리 이름 — 줄은 처음 자리 아래 한 번만 나온다")
    state: Literal["open", "drafted", "rejected"] = Field(description="open: 아직 안 답함 · drafted: 초안 만듦(detect 항목) · rejected: 기각함")
    actions: list[Literal["draft", "reject"]] = Field(description="open이면 [draft, reject], 답했으면 빈 목록")
    answered_at: Optional[str] = Field(description="답한 시각")
    new_item: Optional[str] = Field(description="drafted: 만든 detect 항목 id")
    adr: Optional[str] = Field(description="drafted: 초안의 ADR 번호")
    message: Optional[str] = Field(description="초안 만들기가 초안 없이 끝났으면 그 이유 (줄은 열린 채로)")


class CheckGroup(BaseModel):
    slot: str = Field(description="자리 id (radar-slots.yaml)")
    name: Optional[str] = Field(description="자리 이름")
    repo: Optional[str] = Field(description="그 자리의 대상 repo")
    rows: list[CheckRow]


class CheckDetail(BaseModel):
    """자리 질의 「확인 필요」 (HYO-245) — 그 주 후보를 자리마다 묶은 목록. 다른 데서 이미 거부한 소식은 빠져 있다."""
    week: str
    open: int = Field(description="아직 안 답한 줄 수 — 0이 되면 항목이 Inbox에서 빠진다")
    total: int = Field(description="보이는 줄 수 (답한 줄 포함)")
    groups: list[CheckGroup]


class CheckRowRequest(BaseModel):
    row: str = Field(description="CheckRow.id")


class InboxDetail(InboxItem):
    draft: Optional[AdrDraft] = Field(description="편집용 초안. detect는 초안 파일을 읽어 만든다 — 못 읽으면 null")
    markdown: str = Field(description="저장될(된) ADR 파일")
    changes: list[str] = Field(description="대기 중 바뀐 점 (지문 비교). 비면 그대로")
    number_note: str
    note: Optional[str] = Field(default=None, description="답할 수 없을 때만")
    affected_errors: list[str] = Field(default=[], description="affected repo가 레이더 대상이 아니면 그 이유 (R18) — 그대로 승인하면 쓰지 않는다. 고쳐서 승인한다")
    usage: Optional[Usage] = Field(default=None, description="이 항목을 만드는 데 든 모델 사용량 (capture 1턴). detect 초안은 실행(GET /runs)에 있다")
    check: Optional[CheckDetail] = Field(default=None, description="check만: 자리마다 묶은 후보 목록 (markdown은 빈 문자열)")


class AnswerRequest(BaseModel):
    stale_answer: Optional[StaleAnswer] = Field(default=None, description="바뀐 점(stale)을 본 뒤의 답")


class EditRequest(BaseModel):
    draft: dict[str, Any] = Field(description="고친 초안 (AdrDraft 모양). 검사는 서버가 한다 — status·번호 칸이 있으면 edit_errors")
    stale_answer: Optional[StaleAnswer] = None


Outcome = Literal["written", "rejected", "stale", "edit_errors", "discarded", "rejudge", "blocked", "recovered", "acked", "error",
                  "drafted", "no_draft",  # 「확인 필요」 초안 만들기 (HYO-245) — detect 항목이 생김 · 초안 없이 끝남(줄은 열린 채)
                  "queued"]  # 답을 접수했다 — ADR repo 반영(git·PR)은 export 작업이 뒤에서 한다 (HYO-217, 실사용 모드)


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
    new_item: Optional[str] = Field(default=None, description="rejudge로 새로 생긴 대기 항목 · 「확인 필요」 초안 만들기로 생긴 detect 항목")
    blocked: Optional[dict[str, Any]] = None
    usage: Optional[Usage] = Field(default=None, description="이 답에서 모델을 불렀으면 그 사용량 (capture 2턴 · 「확인 필요」 초안 만들기). 모델을 안 부르면 null")
    check: Optional[CheckDetail] = Field(default=None, description="「확인 필요」 답의 뒤 목록 — 열린 줄이 없으면(항목이 Inbox에서 빠짐) null")


# ── capture ───────────────────────────────────────────────────────

class TopicRef(BaseModel):
    id: str = Field(description="화제 id (GET /topics의 TopicRow.id)")
    week: str = Field(description="화제가 속한 주차 (예 2026-W42)")


class CaptureRequest(BaseModel):
    conversation: str = Field(description="조사 대화 원문 (20만 자까지)")
    repo: Optional[str] = Field(default=None, description="사람이 정한 대상 repo (GET /targets 중 하나). 없으면 「자동」 — 모델이 대화에서 찾는다 (HYO-95)")
    topic: Optional[TopicRef] = Field(default=None, description="화제 탭에서 연 capture면 그 토픽 — 행동 로그(capture_submitted)에만 남는다. capture 동작은 그대로")


class Target(BaseModel):
    repo: str


class TargetList(BaseModel):
    items: list[Target] = Field(description="레이더 대상 repo (radar-targets.json) — 빠른 capture의 repo 드롭다운")


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


class ExperimentPlan(BaseModel):
    """이 결정에 맞는 실험 레시피 (HYO-116, `pipeline.experiment.plan`) — 돌리지 않고 무엇을 돌릴지만. ready가 False면 reason."""
    adr: str
    recipe: Optional[str] = Field(description="dependency(의존성 올려 보기) · embedding(임베딩 정답지 비교) · 없으면 null — 맞는 레시피가 아직 없다")
    label: Optional[str] = None
    cost: Optional[Literal["local", "subscription", "api"]] = Field(default=None, description="local만 바로 돈다 — subscription · api는 확인이 필요하다")
    cost_text: Optional[str] = None
    ready: bool
    reason: Optional[str] = Field(default=None, description="못 돌리는 이유 (레시피 없음 · 버전 못 읽음 · 검사 설정 없음 · 돌릴 곳 없음)")
    detail: Optional[str] = Field(default=None, description="무엇을 어디서 — 「fastapi → 0.162.6 (decision-radar · desktop) — …」")
    export_rule: Optional[str] = Field(default=None, description="내보내기(브랜치 push · draft PR) 조건")
    exports: bool = Field(default=False, description="이 레시피가 내보내기를 하나 — 임베딩 비교는 근거만 낸다(false, 화면은 체크 상자를 숨긴다)")
    export_allowed: bool = Field(default=False, description="이 모드에서 내보내기를 켤 수 있나 — 실사용 모드이고 레시피가 내보낼 때만")


class ExperimentExport(BaseModel):
    branch: Optional[str] = None
    pr_url: Optional[str] = None
    skipped_reason: Optional[str] = None


class ExperimentResult(BaseModel):
    """실험 한 번의 결과 — 결정은 사람이다(ADR · status를 바꾸지 않는다)."""
    adr: str
    recipe: Optional[str]
    label: Optional[str]
    at: str
    seconds: Optional[float] = None
    verdict: str = Field(description="레시피의 판정 (올려 보기: pass · fail · pass_not_exercised · install_failed …) · unavailable · needs_confirm")
    verdict_text: str
    ok: bool
    summary: str
    report_md: Optional[str] = None
    export: Optional[ExperimentExport] = None
    stub: bool = Field(default=False, description="스텁 · 데모 — 실제로 돌리지 않았다")


class DecisionDetail(Decision):
    markdown: str
    experiment: Optional[ExperimentPlan] = Field(default=None, description="실험해 보기 (HYO-116) — 이 결정에 맞는 레시피")
    experiments: list[ExperimentResult] = Field(default_factory=list, description="이 결정의 지난 실험, 최근 것 먼저")


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

JobKind = Literal["capture", "detect", "revisit", "experiment", "export"]


class JobRequest(BaseModel):
    conversation: Optional[str] = Field(default=None, description="capture만")
    repo: Optional[str] = Field(default=None, description="capture만 — CaptureRequest.repo와 같다")
    topic: Optional[TopicRef] = Field(default=None, description="capture만 — CaptureRequest.topic과 같다")
    adr: Optional[str] = Field(default=None, description="experiment만 — 실험할 결정 번호 (HYO-116)")
    export: Optional[bool] = Field(default=None, description="experiment만 — 레시피 조건(예: 통과)이면 브랜치 push + draft PR. 실사용 모드만")


class Job(BaseModel):
    id: str
    kind: JobKind
    state: Literal["queued", "running", "done", "error"]
    created: str
    started: Optional[str]
    finished: Optional[str]
    message: Optional[str] = Field(description="한 줄 — 상태 창에 그대로")
    result: Union[CaptureResult, RunResult, ExperimentResult, None] = Field(
        description="끝났을 때: capture → CaptureResult, detect·revisit → RunResult, experiment → ExperimentResult")
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
    event: str = Field(description="app_opened · item_opened · notification_shown · notification_clicked · capture_opened · decision_opened · topics_opened(화제 탭을 엶) · topic_opened(화제의 링크를 누름, item=화제 id)")
    item: Optional[str] = None
    detail: Optional[dict[str, Any]] = Field(default=None, description="notification_shown·notification_clicked는 GET /notifications가 읽는 칸: "
                                                                        "kind · title · body(OS 알림 문구) · items(묶은 알림의 항목 id 목록 — 없으면 [item]) · "
                                                                        "nid(알림 하나의 id — shown·clicked가 같은 값이면 정확히 짝짓는다)")


# ── 지난 알림 (HYO-110) — 행동 로그(notification_shown)를 읽기만 한다. 따로 저장하지 않는다 ─────

class NotificationItem(BaseModel):
    """알림이 가리킨 항목 하나의 지금 상태. 화면은 inbox면 Inbox 항목으로, answered + adr이면 Decisions로 보낸다."""
    id: str
    status: Literal["inbox", "answered", "gone"] = Field(description="inbox: 지금 Inbox에 있다 · answered: 답했다(승인·수정·거부·확인함) · "
                                                                   "gone: 둘 다 아니다 (예전 알림의 실행 경로 kind — run_drafts·run_alerts — 는 item이 ADR 번호)")
    kind: Optional[InboxKind] = Field(description="inbox일 때 Inbox 항목 종류")
    title: Optional[str] = Field(description="inbox일 때 Inbox 항목 제목")
    state: Optional[ItemState] = Field(description="inbox일 때 Inbox 상태")
    answer: Optional[Action] = Field(description="answered: 마지막 답")
    outcome: Optional[Outcome] = Field(description="answered: 마지막 답의 결과")
    answered_at: Optional[str]
    adr: Optional[str] = Field(description="이 항목의 ADR 번호 — revisit 항목, 또는 승인으로 쓴 ADR. 거부·버림·다시 판정으로 지운 초안은 없음")
    new_item: Optional[str] = Field(description="rejudge(다시 판정)로 새로 생긴 대기 항목")
    opened: bool = Field(description="이 알림 뒤에 항목을 열어 봤나 (item_opened)")


class NotificationEntry(BaseModel):
    """OS 알림 한 통 (notification_shown 한 줄). 「읽음」은 clicked 또는 항목의 opened — 조용히 표시만 한다 (배지 없음)."""
    id: str = Field(description="행동 로그의 줄 번호 기반 id — 같은 데이터 폴더에서 바뀌지 않는다")
    at: str = Field(description="알림이 뜬 시각")
    kind: Optional[str] = Field(description="화면이 보낸 detail.kind 그대로 (예 capture · detect · revisit, 예전 것은 inbox_new · run_drafts · run_alerts)")
    title: Optional[str] = Field(description="OS 알림 제목 (detail.title). 화면이 안 보냈으면 null")
    body: Optional[str] = Field(description="OS 알림 본문 (detail.body). 화면이 안 보냈으면 null")
    clicked: bool = Field(description="이 알림을 눌렀나 (notification_clicked — nid가 같거나, 없으면 같은 item·kind의 바로 앞 알림에 짝짓는다)")
    clicked_at: Optional[str]
    items: list[NotificationItem] = Field(description="알림이 가리킨 항목 (묶은 알림이면 여럿, item 없는 알림이면 빈 목록)")


class NotificationList(BaseModel):
    items: list[NotificationEntry] = Field(description="최근 것 먼저")


# ── 화제 (레인 C, HYO-127) ───────────────────────────────────────

class TopicPost(BaseModel):
    source: str = Field(description="hn · reddit · github · huggingface")
    title: str
    url: str
    signal: str = Field(description="인기 한 마디 (예 `HN 2,123점` · `r/LocalLLaMA 주간 3위`) — 없으면 빈 문자열")


class TopicParentSuggestion(BaseModel):
    """「~의 하위로 보임」 제안 (HYO-152) — 규칙(P1·P2)이 같은 주 다른 화제를 부모로 짚었다. 자동으로 숨기거나 접지 않는다."""
    topic_id: str = Field(description="부모 후보 화제 id")
    title: str = Field(description="부모 후보 화제 제목")
    url: str = Field(description="부모 후보 화제 대표 글 링크")
    rank: int = Field(description="부모 후보의 그 주 순위")
    rule: Literal["P1-prefix", "P1-shared", "P2"] = Field(description="발화한 짝 다수의 갈래 — P1-prefix: 이름 앞부분 · P1-shared: 앞 2토막 공유 · P2: 활용 언급")
    pairs: list[list[str]] = Field(description="발화한 글 짝 [자식 글 id, 부모 글 id]")
    verdict: Optional[Literal["accept", "reject"]] = Field(default=None, description="이 자식·부모에 낸 마지막 응답 (맞음/아님) — 아직 안 답했으면 null")


class TopicParentManual(BaseModel):
    topic_id: str = Field(description="사용자가 직접 묶은 부모 화제 id")
    title: str = Field(description="그 화제 제목")


class TopicRow(BaseModel):
    id: str = Field(description="토픽 id (t-<해시>) — 같은 글 묶음이면 주를 다시 만들어도 같다")
    week: str
    rank: int = Field(description="그 주의 순위 (1부터)")
    section: Literal["multi", "single"] = Field(description="multi: 여러 출처에 나온 화제 자리 · single: 한 출처 화제 자리")
    title: str = Field(description="가장 인기 있는 글의 제목")
    url: str = Field(description="그 글의 링크")
    n_sources: int
    sources: dict[str, int] = Field(description="출처별 글 수 (크로스포스트는 한 글)")
    n_items: int
    signal: str = Field(description="대표 글의 인기 한 마디")
    posts: list[TopicPost] = Field(description="토픽에 묶인 모든 글 (파일 순서 = 인기 순)")
    saved: bool = Field(description="저장한 화제인가")
    capture_seed: str = Field(description="이 화제에서 capture 창을 열 때 미리 채울 글 — 결정 질문은 사람이 이어 쓴다")
    parent_suggestion: Optional[TopicParentSuggestion] = Field(default=None, description="규칙이 짚은 부모 화제 제안 — 없으면 null (HYO-152)")
    parent_manual: Optional[TopicParentManual] = Field(default=None, description="사용자가 직접 묶은 부모 화제 (마지막 직접 묶기, 풀면 null)")


class TopicList(BaseModel):
    week: Optional[str] = Field(description="보여 주는 주차. 화제 파일이 하나도 없으면 null")
    weeks: list[str] = Field(description="있는 주차 — 새 것 먼저")
    method: Optional[str] = Field(description="묶기 방법 이름 (예 link+crosspost@v1)")
    k: int = Field(description="이 주차가 기본으로 보이는 상위 개수 (절반은 여러 출처 자리)")
    max_limit: int = Field(description="「더 보기」로 펼칠 수 있는 최대 개수 (HYO-154) — min(120, 그 주 토픽 수). k 아래는 파일 순위 그대로")
    counts: Optional[dict[str, Any]] = Field(description="그 주 건수 (후보 · 토픽 · 출처를 넘은 토픽 …) — 파이프라인이 쓴 그대로")
    items: list[TopicRow] = Field(description="상위 limit개 (없으면 k개), 순위대로")


class WeekListDetect(BaseModel):
    """detect가 이 줄을 어떻게 했나 (HYO-171) — 이 기기의 detect 실행 기록에서 읽을 때 붙인다. list.json에는 없다."""
    verdict: Literal["draft", "existing", "excluded", "dropped", "failed", "unjudged"] = Field(
        description="draft 초안 씀 · existing 이미 있는 결정과 겹쳐 건너뜀(R14) · excluded 버림(모델 · 이미 반영됨 · 이전에 거부한 소식) · "
                    "dropped 대상 repo가 안 맞아 버림(R10) · failed 초안 쓰다 실패 · unjudged detect가 돌았는데 이 줄을 언급하지 않았다")
    reason: Optional[str] = Field(default=None, description="버린 · 실패한 이유. draft · existing · unjudged는 null")
    adr: Optional[str] = Field(default=None, description="draft면 쓴 초안, existing이면 겹친 ADR 번호 (예 0017)")


class WeekListRun(BaseModel):
    id: str = Field(description="detect 실행 id (GET /runs)")
    at: str
    trigger: str = Field(description="manual · schedule · job")
    recorded: bool = Field(description="false면 url별 판정을 남기기 전(11/5)의 실행 — 버린 소식만 붙고 초안 · 겹침은 unjudged로 보인다")


class WeekListItem(BaseModel):
    """이번 주 목록의 한 줄 (HYO-168 · HYO-170 — docs/design/query-topk.md). 칸마다 쓰는 필드가 다르다 — 안 쓰면 null."""
    id: str = Field(description="수집 항목 id (스냅숏 items.jsonl의 id)")
    title: Optional[str] = None
    url: Optional[str] = None
    tier: Optional[str] = Field(default=None, description="관문 등급 L0~L8 (「항상」 · 기술 질의 · 경보). 자리 질의 · 우리 스택 아님은 null")
    change: Optional[str] = Field(default=None, description="변화 종류 (security · eol · model_retirement · breaking · pricing …)")
    reason: Optional[str] = Field(default=None, description="왜 왔나 한 줄 — 관문 행(`electron L1 × security`) · 자리 적합도 이유 · 경보 그물")
    rank: Optional[int] = Field(default=None, description="질의 안 순위 (1부터). 「항상」 · 경보는 null")
    to_detect: bool = Field(default=False, description="detect 입력으로 갔나 (초안 후보)")
    folded_into: Optional[str] = Field(default=None, description="같은 패키지 · 줄의 더 새 버전 또는 같은 사고의 다른 글로 접혔으면 그 대표 id — 화면은 「외 N건」으로 묶는다")
    alarm: Optional[list[str]] = Field(default=None, description="경보 그물(HYO-170)로 온 글이면 걸린 스택 · 런타임 이름들")
    source: Optional[str] = Field(default=None, description="출처 (vendor · hn · reddit · huggingface …) — 자리 질의 · 우리 스택 아님")
    fit: Optional[int] = Field(default=None, description="자리 적합도 0~3 (자리 질의만)")
    points: Optional[int] = Field(default=None, description="HN 점수 (우리 스택 아님만, Reddit은 null)")
    detect: Optional[WeekListDetect] = Field(default=None, description="to_detect인 줄만 — 그 주 입력으로 돈 detect가 있으면 판정. 아직 안 돌았으면 null")


class WeekListTech(BaseModel):
    query: str = Field(description="기술 이름 (관문의 기술 — 등급 파일 이름)")
    items: list[WeekListItem] = Field(description="그 기술의 top-k, 순위대로")


class WeekListSlot(BaseModel):
    slot: str = Field(description="자리 id (ADR repo radar-slots.yaml)")
    name: Optional[str] = Field(default=None, description="자리 이름 (예 관문 분류)")
    current: list[str] = Field(default_factory=list, description="지금 그 자리에 있는 모델 · 서비스")
    items: list[WeekListItem] = Field(description="새 후보 top-k (적합도 문턱 이상) — 없으면 빈 목록 = 「없음」")


class SourceHealthReason(BaseModel):
    rule: Literal["failed", "zero", "half", "moved", "stale", "unregistered"] = Field(
        description="걸린 규칙 — unregistered: 데이터 등록부에 없는 항목 출처 · 통로 (HYO-180). collector/health.py RULES와 같은 목록")
    detail: str = Field(description="사람이 읽는 근거 한 줄")


class SourceHealthProblem(BaseModel):
    key: str
    kind: Literal["source", "channel", "page", "feed", "url"] = Field(description="검사 단위 — channel: 등록부에 없는 통로 종류 (HYO-180)")
    name: str = Field(description="출처 이름")
    url: Optional[str] = None
    reasons: list[SourceHealthReason]
    streak: int = Field(description="몇 주째 이상인가")
    since: Optional[str] = Field(default=None, description="처음 이상이 보인 주차")


class SourceHealth(BaseModel):
    week: str
    line: str = Field(description="한 줄 요약 — 이상이 없으면 「출처 이상 없음 (N곳)」")
    checked: int = Field(description="검사한 출처 수")
    problems: list[SourceHealthProblem]


class WeekList(BaseModel):
    """이번 주 목록 — 수집 PC가 만든 `detect/<주차>/list.json` (pipeline.detect_feed). 화면은 칸마다 묶어 한 줄씩 보인다."""
    week: Optional[str] = Field(description="보여 주는 주차. 목록 파일이 하나도 없으면 null")
    weeks: list[str] = Field(description="목록이 있는 주차 — 새 것 먼저")
    always: list[WeekListItem] = Field(description="「항상」 — 보안 · 우리 버전 EOL · 모델 은퇴. 전부 보이고 folded_into가 있으면 대표 아래로 접는다")
    tech: list[WeekListTech] = Field(description="기술 질의 — 기술마다 top-k")
    slots: list[WeekListSlot] = Field(description="자리 질의 — 자리마다 새 후보 top-k")
    alarm: list[WeekListItem] = Field(description="경보 — 관문이 모르는 런타임 · 도구의 사고 글 (HYO-170)")
    not_ours: list[WeekListItem] = Field(description="우리 스택 아님 — 그 주 큰 사고 중 우리 스택에 안 걸린 것 (보고 넘겼다는 표시)")
    counts: dict[str, int] = Field(description="칸별 건수 + to_detect")
    detect_run: Optional[WeekListRun] = Field(default=None, description="이 주 입력으로 끝까지 돈 마지막 detect (이 기기 기록). 없으면 null — 줄의 detect도 null")
    source_health: Optional[SourceHealth] = Field(default=None, description="출처 건강 검사 (HYO-172) — 수집 PC가 얼릴 때 쓴 `<주차>/source-health.json`. 그 주 파일이 없거나 못 읽으면 null")


class SavedTopic(BaseModel):
    """저장한 화제 — 복사본이라 주차 파일이 다시 만들어지거나 지워져도 남는다."""
    id: str
    week: str
    title: str
    url: str
    n_sources: int
    sources: dict[str, int]
    n_items: int
    posts: list[TopicPost]
    saved_at: str


class SavedTopicList(BaseModel):
    items: list[SavedTopic] = Field(description="최근에 저장한 것 먼저")


class TopicSaveRequest(BaseModel):
    week: str = Field(description="화제가 속한 주차 — 그 주 파일에서 복사해 저장한다")


class TopicActionResult(BaseModel):
    ok: bool
    saved: bool = Field(description="지금 저장된 상태인가")
    message: str


class TopicParentFeedbackRequest(BaseModel):
    week: str = Field(description="화제가 속한 주차")
    parent_id: str = Field(description="답하는 부모 제안의 화제 id — 지금 계산되는 제안과 같아야 한다")
    verdict: Literal["accept", "reject"] = Field(description="accept: 맞음(하위가 맞다) · reject: 아님(하위가 아니다)")


class TopicParentRequest(BaseModel):
    week: str = Field(description="화제가 속한 주차")
    parent_id: Optional[str] = Field(description="직접 묶을 부모 화제 id (같은 주). null이면 직접 묶은 부모를 푼다")


class TopicParentResult(BaseModel):
    ok: bool
    verdict: Optional[Literal["accept", "reject", "manual", "manual_clear"]] = Field(description="저장한 응답 — 저장할 게 없었으면 null")
    message: str


class TopicParentCandidate(BaseModel):
    id: str
    title: str
    rank: int


class TopicParentCandidateList(BaseModel):
    items: list[TopicParentCandidate] = Field(description="제목에 검색어가 든 같은 주 화제, 순위순")

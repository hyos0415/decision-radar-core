// 타입은 백엔드 계약에서 생성한다 (HYO-91) — 손으로 고치지 않는다.
//   백엔드 desktop/backend/schemas.py → python desktop/backend/export_openapi.py → desktop/openapi.json
//   → npm run gen:api → ./api.gen.ts (openapi-typescript). CI가 다시 뽑아 차이가 있으면 실패한다.
// 여기서는 화면 코드가 쓰던 이름으로 별칭만 준다.
import createClient from 'openapi-fetch'
import type { components, paths } from './api.gen'
import { COPY } from '../../../copy'

type S = components['schemas']

export type Affected = S['Affected']
export type Evidence = S['Evidence']
export type RevisitWhen = S['RevisitCondition']
export type AdrDraft = S['AdrDraft']

export type InboxItem = S['InboxItem']
export type InboxDetail = S['InboxDetail']
export type Kind = InboxItem['kind']
export type ItemState = InboxItem['state']
export type Action = InboxItem['actions'][number]
export type StaleAnswer = NonNullable<S['AnswerRequest']['stale_answer']>

export type ActionResult = S['ActionResult']
export type Outcome = ActionResult['outcome']
export const SUCCESS_OUTCOMES: Outcome[] = [
  'written',
  'rejected',
  'discarded',
  'rejudge',
  'recovered',
  'acked',
  'queued' // 답을 접수했다 — ADR repo 반영은 뒤에서 (HYO-217)
]

/** 자리 질의 「확인 필요」 (HYO-245) — 주당 Inbox 항목 하나, 자리마다 후보 목록 */
export type CheckDetail = S['CheckDetail']
export type CheckGroup = S['CheckGroup']
export type CheckRow = S['CheckRow']

export type CaptureResult = S['CaptureResult']

export type DecisionRevisit = S['DecisionRevisit']
export type RevisitSummary = S['RevisitSummary']
export type Decision = S['Decision']
export type DecisionDetail = S['DecisionDetail']
export type ExperimentPlan = S['ExperimentPlan']
export type ExperimentResult = S['ExperimentResult']

export type RunResult = S['RunResult']
export type Flow = RunResult['flow']

/** 지난 알림 (HYO-110·113) — OS 알림 한 통과, 그 알림이 가리킨 항목의 지금 상태 */
export type NotificationEntry = S['NotificationEntry']

/** 화제 탭 (HYO-133) · 부모 후보 제안 (HYO-152) */
export type TopicList = S['TopicList']
export type TopicRow = S['TopicRow']
export type TopicPost = S['TopicPost']
export type SavedTopic = S['SavedTopic']
export type TopicParentSuggestion = S['TopicParentSuggestion']
export type TopicParentCandidate = S['TopicParentCandidate']
export type TopicParentResult = S['TopicParentResult']
export type TopicParentVerdict = S['TopicParentFeedbackRequest']['verdict']

/** 이번 주 목록 (HYO-168 · HYO-170) — 수집 PC가 만든 list.json을 읽기만 한다 */
export type WeekList = S['WeekList']
export type WeekListItem = S['WeekListItem']

export type Health = S['Health']
export type Mode = Health['mode']

/** 작업 + 폴링 (HYO-85) — 접수하면 바로 돌아온다. 끝나면 Inbox를 다시 읽고 알림을 띄운다 */
export type Job = S['Job']
export type JobKind = Job['kind']
export type JobState = Job['state']
export type Schedule = S['Schedule']

export type EventName = 'item_opened' | 'app_opened' | 'decision_opened' | 'capture_opened'

// 호출은 openapi-fetch — 경로 문자열이 요청 본문·경로 인자·응답 타입을 정한다. 없는 경로·틀린 본문은 컴파일 오류다 (HYO-91).
// 화면은 아래 `api.*`만 쓴다 (이름·인자는 손으로 쓰던 때와 같다).
let client = createClient<paths>({ baseUrl: '' })

export function setBaseUrl(url: string): void {
  client = createClient<paths>({ baseUrl: url })
}

/** 오류면 백엔드의 `detail` 한 줄로 던진다 (422 입력 오류는 detail이 목록 — 첫 메시지). 성공이면 응답 본문 */
async function unwrap<T>(
  p: Promise<{ data?: T; error?: unknown; response: Response }>
): Promise<T> {
  let res: Awaited<typeof p>
  try {
    res = await p
  } catch (e) {
    // fetch throws a TypeError (「Failed to fetch」) when the backend cannot be reached
    if (e instanceof TypeError) throw new Error(COPY.app.connectFailed)
    throw e
  }
  const { data, error, response } = res
  if (error !== undefined || data === undefined) {
    const d = (error as { detail?: unknown } | undefined)?.detail
    const msg =
      typeof d === 'string'
        ? d
        : Array.isArray(d) && typeof d[0]?.msg === 'string'
          ? d[0].msg
          : COPY.app.requestFailed(response.status)
    throw new Error(msg)
  }
  return data
}

export const api = {
  health: () => unwrap(client.GET('/health')),
  inbox: () => unwrap(client.GET('/inbox')).then((r) => r.items),
  detail: (id: string) =>
    unwrap(client.GET('/inbox/{item_id}', { params: { path: { item_id: id } } })),
  approve: (id: string, stale_answer: StaleAnswer | null = null) =>
    unwrap(
      client.POST('/inbox/{item_id}/approve', {
        params: { path: { item_id: id } },
        body: { stale_answer }
      })
    ),
  edit: (id: string, draft: AdrDraft, stale_answer: StaleAnswer | null = null) =>
    unwrap(
      client.POST('/inbox/{item_id}/edit', {
        params: { path: { item_id: id } },
        body: { draft, stale_answer }
      })
    ),
  reject: (id: string) =>
    unwrap(client.POST('/inbox/{item_id}/reject', { params: { path: { item_id: id } } })),
  /** 「확인함」 — 접수된 capture(끝난 것)·revisit 충족 알림을 Inbox에서 치운다. ADR은 안 바꾼다 (R8) */
  ack: (id: string) =>
    unwrap(client.POST('/inbox/{item_id}/ack', { params: { path: { item_id: id } } })),
  /** 「확인 필요」 후보 하나로 초안 만들기 — 그 한 건만 detect (모델을 부른다). 초안은 detect 항목으로 Inbox에 온다 */
  checkDraft: (id: string, row: string) =>
    unwrap(
      client.POST('/inbox/{item_id}/check/draft', {
        params: { path: { item_id: id } },
        body: { row }
      })
    ),
  /** 「확인 필요」 후보 하나 기각 — 같은 소식은 다시 올라오지 않는다 */
  checkReject: (id: string, row: string) =>
    unwrap(
      client.POST('/inbox/{item_id}/check/reject', {
        params: { path: { item_id: id } },
        body: { row }
      })
    ),
  /** 「확인 필요」 남은 후보 전부 기각 — 항목이 Inbox에서 빠진다 */
  checkRejectRest: (id: string) =>
    unwrap(
      client.POST('/inbox/{item_id}/check/reject-rest', { params: { path: { item_id: id } } })
    ),
  event: (event: EventName, item?: string): void => {
    unwrap(client.POST('/events', { body: { event, item: item ?? null, detail: {} } })).catch(
      () => {}
    )
  },
  capture: (conversation: string) => unwrap(client.POST('/capture', { body: { conversation } })),
  decisions: () => unwrap(client.GET('/decisions')).then((r) => r.items),
  decision: (id: string) =>
    unwrap(client.GET('/decisions/{adr_id}', { params: { path: { adr_id: id } } })),
  runs: () => unwrap(client.GET('/runs')).then((r) => r.items),
  /** 지난 알림, 최근 것 먼저 — 행동 로그를 읽기만 한다 */
  notifications: (limit = 100) =>
    unwrap(client.GET('/notifications', { params: { query: { limit } } })).then((r) => r.items),
  submitJob: (kind: JobKind, conversation?: string) =>
    unwrap(
      client.POST('/jobs/{kind}', {
        params: { path: { kind } },
        body: conversation === undefined ? {} : { conversation }
      })
    ).then((r) => r.job),
  /** 실험해 보기 (HYO-116) — 결정 하나를 레시피로. 돌릴 수 없으면 409(이유). exportPr = 레시피 조건이면 draft PR (실사용 모드만) */
  submitExperiment: (adr: string, exportPr: boolean) =>
    unwrap(
      client.POST('/jobs/{kind}', {
        params: { path: { kind: 'experiment' } },
        body: { adr, export: exportPr }
      })
    ).then((r) => r.job),
  /** 그 주의 상위 화제 — week를 안 주면 가장 새 주차. 화제 파일이 없으면 week가 null.
   *  limit은 「더 보기」(HYO-154) — 없으면 k개, 많아야 max_limit개 */
  topics: (week?: string, limit?: number) =>
    unwrap(
      client.GET('/topics', {
        params: { query: { ...(week ? { week } : {}), ...(limit ? { limit } : {}) } }
      })
    ),
  /** 이번 주 목록 — week를 안 주면 가장 새 주차. 목록 파일이 없으면 week가 null */
  weekList: (week?: string) =>
    unwrap(client.GET('/week-list', { params: { query: week ? { week } : {} } })),
  savedTopics: () => unwrap(client.GET('/topics/saved')).then((r) => r.items),
  saveTopic: (id: string, week: string) =>
    unwrap(
      client.POST('/topics/{topic_id}/save', {
        params: { path: { topic_id: id } },
        body: { week }
      })
    ),
  unsaveTopic: (id: string) =>
    unwrap(client.POST('/topics/{topic_id}/unsave', { params: { path: { topic_id: id } } })),
  /** 부모 제안에 맞음(accept) · 아님(reject)으로 답한다 — 다시 부르면 답이 바뀐다 */
  topicParentFeedback: (id: string, week: string, parent_id: string, verdict: TopicParentVerdict) =>
    unwrap(
      client.POST('/topics/{topic_id}/parent-feedback', {
        params: { path: { topic_id: id } },
        body: { week, parent_id, verdict }
      })
    ),
  /** 직접 묶기 — parent_id가 null이면 풀기 */
  topicSetParent: (id: string, week: string, parent_id: string | null) =>
    unwrap(
      client.POST('/topics/{topic_id}/parent', {
        params: { path: { topic_id: id } },
        body: { week, parent_id }
      })
    ),
  topicParentCandidates: (week: string, q: string, limit = 8) =>
    unwrap(client.GET('/topics/parent-candidates', { params: { query: { week, q, limit } } })).then(
      (r) => r.items
    ),
  jobs: () => unwrap(client.GET('/jobs')).then((r) => r.items),
  schedule: () => unwrap(client.GET('/schedule')),
  job: (id: string) => unwrap(client.GET('/jobs/{job_id}', { params: { path: { job_id: id } } }))
}

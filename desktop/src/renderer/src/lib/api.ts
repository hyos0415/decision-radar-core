// 타입은 백엔드 계약에서 생성한다 (HYO-91) — 손으로 고치지 않는다.
//   백엔드 desktop/backend/schemas.py → python desktop/backend/export_openapi.py → desktop/openapi.json
//   → npm run gen:api → ./api.gen.ts (openapi-typescript). CI가 다시 뽑아 차이가 있으면 실패한다.
// 여기서는 화면 코드가 쓰던 이름으로 별칭만 준다.
import createClient from 'openapi-fetch'
import type { components, paths } from './api.gen'

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
  'acked'
]

export type CaptureResult = S['CaptureResult']

export type DecisionRevisit = S['DecisionRevisit']
export type RevisitSummary = S['RevisitSummary']
export type Decision = S['Decision']
export type DecisionDetail = S['DecisionDetail']

export type RunResult = S['RunResult']
export type Flow = RunResult['flow']

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
  const { data, error, response } = await p
  if (error !== undefined || data === undefined) {
    const d = (error as { detail?: unknown } | undefined)?.detail
    const msg =
      typeof d === 'string'
        ? d
        : Array.isArray(d) && typeof d[0]?.msg === 'string'
          ? d[0].msg
          : `요청 실패 (${response.status})`
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
  submitJob: (kind: JobKind, conversation?: string) =>
    unwrap(
      client.POST('/jobs/{kind}', {
        params: { path: { kind } },
        body: conversation === undefined ? {} : { conversation }
      })
    ).then((r) => r.job),
  jobs: () => unwrap(client.GET('/jobs')).then((r) => r.items),
  schedule: () => unwrap(client.GET('/schedule')),
  job: (id: string) => unwrap(client.GET('/jobs/{job_id}', { params: { path: { job_id: id } } }))
}

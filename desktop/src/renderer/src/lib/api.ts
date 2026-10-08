// 타입은 백엔드 계약에서 생성한다 (HYO-91) — 손으로 고치지 않는다.
//   백엔드 desktop/backend/schemas.py → python desktop/backend/export_openapi.py → desktop/openapi.json
//   → npm run gen:api → ./api.gen.ts (openapi-typescript). CI가 다시 뽑아 차이가 있으면 실패한다.
// 여기서는 화면 코드가 쓰던 이름으로 별칭만 준다.
import type { components } from './api.gen'

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

export type EventName = 'item_opened' | 'app_opened' | 'decision_opened' | 'capture_opened'

let baseUrl = ''

export function setBaseUrl(url: string): void {
  baseUrl = url
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const res = await fetch(baseUrl + path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`
    try {
      const j = await res.json()
      if (j && typeof j.detail === 'string') detail = j.detail
    } catch {
      // keep default
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

const enc = encodeURIComponent

export const api = {
  health: () => request<Health>('GET', '/health'),
  inbox: () => request<{ items: InboxItem[] }>('GET', '/inbox').then((r) => r.items),
  detail: (id: string) => request<InboxDetail>('GET', `/inbox/${enc(id)}`),
  approve: (id: string, stale_answer: StaleAnswer | null = null) =>
    request<ActionResult>('POST', `/inbox/${enc(id)}/approve`, { stale_answer }),
  edit: (id: string, draft: AdrDraft, stale_answer: StaleAnswer | null = null) =>
    request<ActionResult>('POST', `/inbox/${enc(id)}/edit`, { draft, stale_answer }),
  reject: (id: string) => request<ActionResult>('POST', `/inbox/${enc(id)}/reject`, {}),
  /** 「확인함」 — 접수된 capture(끝난 것)·revisit 충족 알림을 Inbox에서 치운다. ADR은 안 바꾼다 (R8) */
  ack: (id: string) => request<ActionResult>('POST', `/inbox/${enc(id)}/ack`, {}),
  event: (event: EventName, item?: string): void => {
    request('POST', '/events', { event, item: item ?? null, detail: {} }).catch(() => {})
  },
  capture: (conversation: string) => request<CaptureResult>('POST', '/capture', { conversation }),
  decisions: () => request<{ items: Decision[] }>('GET', '/decisions').then((r) => r.items),
  decision: (id: string) => request<DecisionDetail>('GET', `/decisions/${enc(id)}`),
  runs: () => request<{ items: RunResult[] }>('GET', '/runs').then((r) => r.items),
  run: (flow: Flow) => request<RunResult>('POST', `/runs/${flow}`, {}),
  submitJob: (kind: JobKind, conversation?: string) =>
    request<{ job: Job }>(
      'POST',
      `/jobs/${kind}`,
      conversation === undefined ? {} : { conversation }
    ).then((r) => r.job),
  jobs: () => request<{ items: Job[] }>('GET', '/jobs').then((r) => r.items),
  job: (id: string) => request<Job>('GET', `/jobs/${enc(id)}`)
}

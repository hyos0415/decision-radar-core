export type Kind = 'capture' | 'detect'
export type ItemState = 'awaiting_approval' | 'review' | 'stale' | 'blocked' | 'pending'
export type Action = 'approve' | 'edit' | 'reject'
export type StaleAnswer = 'continue' | 'rejudge' | 'discard'

export type Affected = { repo: string; component: string }
export type Evidence = {
  url: string
  source_type: 'official_doc' | 'release_note' | 'repo' | 'paper' | 'issue_pr' | 'community'
}
export type RevisitWhen = {
  id: string
  check_type: 'web' | 'repo' | 'numeric' | 'human'
  condition: string
  source_hint: string | null
  threshold?: { metric: string; op: '<' | '<=' | '>' | '>=' | '=='; value: number }
  basis?: string
}

export type AdrDraft = {
  title: string
  technology: string
  use: string
  affected: Affected[]
  evidence: Evidence[]
  context: string
  decision: string
  related: string[]
  research_note: string[]
  revisit_when: RevisitWhen[]
}

export type InboxItem = {
  id: string
  kind: Kind
  title: string
  technology: string
  affected: Affected[]
  since: string
  waiting_days: number
  state: ItemState
  actions: Action[]
}

export type InboxDetail = InboxItem & {
  draft: AdrDraft | null
  markdown: string
  changes: string[]
  number_note: string
}

export type Outcome =
  | 'written'
  | 'rejected'
  | 'stale'
  | 'edit_errors'
  | 'discarded'
  | 'rejudge'
  | 'blocked'
  | 'recovered'
  | 'error'

export type ActionResult = {
  outcome: Outcome
  message?: string
  adr?: string
  path?: string
  changes?: string[]
  answers?: StaleAnswer[]
  errors?: string[]
}

export const SUCCESS_OUTCOMES: Outcome[] = [
  'written',
  'rejected',
  'discarded',
  'rejudge',
  'recovered'
]

export type CaptureResult = {
  outcome: 'awaiting' | 'linked' | 'no_draft'
  item?: string
  message?: string
  link_candidates?: string[]
}

export type RevisitResultKind = 'met' | 'not_met' | 'undetermined' | string
export type DecisionRevisit = {
  id: string
  check_type: string
  condition: string
  last: { result: RevisitResultKind; checked_at: string; reason: string | null } | null
}
export type RevisitSummary = {
  met: number
  not_met: number
  undetermined: number
  unchecked: number
}
export type Decision = {
  id: string
  title: string
  file: string
  status: string
  technology: string
  use: string
  date: string
  affected: Affected[]
  revisit: DecisionRevisit[]
  revisit_summary: RevisitSummary
  last_checked: string | null
}
export type DecisionDetail = Decision & { markdown: string }

export type Flow = 'detect' | 'revisit'
export type RunResult = {
  id: string
  flow: Flow
  at: string
  mode: string
  ok: boolean
  error: string | null
  summary: string[]
  drafts: { adr: string; title: string; technology?: string }[]
  alerts: { adr: string; condition: string; result: string; message: string }[]
}

export type EventName =
  | 'item_opened'
  | 'app_opened'
  | 'decision_opened'
  | 'capture_opened'

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
  health: () => request<{ ok: boolean; mode: string }>('GET', '/health'),
  inbox: () => request<{ items: InboxItem[] }>('GET', '/inbox').then((r) => r.items),
  detail: (id: string) => request<InboxDetail>('GET', `/inbox/${enc(id)}`),
  approve: (id: string, stale_answer: StaleAnswer | null = null) =>
    request<ActionResult>('POST', `/inbox/${enc(id)}/approve`, { stale_answer }),
  edit: (id: string, draft: AdrDraft, stale_answer: StaleAnswer | null = null) =>
    request<ActionResult>('POST', `/inbox/${enc(id)}/edit`, { draft, stale_answer }),
  reject: (id: string) => request<ActionResult>('POST', `/inbox/${enc(id)}/reject`, {}),
  event: (event: EventName, item?: string): void => {
    request('POST', '/events', { event, item: item ?? null, detail: {} }).catch(() => {})
  },
  capture: (conversation: string) => request<CaptureResult>('POST', '/capture', { conversation }),
  decisions: () => request<{ items: Decision[] }>('GET', '/decisions').then((r) => r.items),
  decision: (id: string) => request<DecisionDetail>('GET', `/decisions/${enc(id)}`),
  runs: () => request<{ items: RunResult[] }>('GET', '/runs').then((r) => r.items),
  run: (flow: Flow) => request<RunResult>('POST', `/runs/${flow}`, {})
}

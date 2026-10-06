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
  event: (event: 'item_opened' | 'app_opened', item?: string): void => {
    request('POST', '/events', { event, item: item ?? null, detail: {} }).catch(() => {})
  }
}

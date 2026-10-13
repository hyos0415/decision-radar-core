import { Notification } from 'electron'
import { getStatus } from './backend'
import type { NavTarget } from '../shared'

// Everything here talks only to the local backend (127.0.0.1) over fetch.

type InboxKind = 'capture' | 'detect' | 'pending' | 'revisit'
type InboxItem = { id: string; kind: InboxKind; title: string }
type RunResult = { id: string; flow: 'detect' | 'revisit' }

type PollerDeps = {
  onClick: (t: NavTarget) => void // show + focus main window and route it
  onPending: (n: number) => void
  onChanged: () => void // data changed: tell the renderer to refresh
}

const POLL_MS = Math.max(1000, Number(process.env.RADAR_POLL_MS) || 5000)
const MAX_NOTIFY_PER_POLL = 3

// Notification title per Inbox item kind (HYO-111) — revisit/pending are not awaiting approval.
const KIND_TITLE: Record<InboxKind, string> = {
  capture: '승인 대기 초안',
  detect: '검토 대기 detect 초안',
  pending: '접수한 capture',
  revisit: '재검토 조건 충족'
}

function base(): string | null {
  const s = getStatus()
  return s.status === 'ready' && s.baseUrl ? s.baseUrl : null
}

async function getJson<T>(path: string): Promise<T | null> {
  const b = base()
  if (!b) return null
  try {
    const res = await fetch(b + path)
    return res.ok ? ((await res.json()) as T) : null
  } catch {
    return null
  }
}

export function postEvent(
  event: string,
  item?: string,
  detail: Record<string, unknown> = {}
): void {
  const b = base()
  if (!b) return
  fetch(b + '/events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ event, item: item ?? null, detail })
  }).catch(() => {})
}

export async function postRun(flow: 'detect' | 'revisit'): Promise<void> {
  const b = base()
  if (!b) return
  try {
    // job + polling (HYO-85): returns as soon as it is accepted; the poller sees the run when it ends
    await fetch(`${b}/jobs/${flow}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}'
    })
  } catch (e) {
    console.error(`[run] ${flow} failed`, e)
  }
}

const live = new Set<Notification>() // keep references so they are not GC'd before click
const knownInbox = new Set<string>()
const knownRuns = new Set<string>()
// detect/revisit items wait here until their run ends, so one run is one notification even when
// a poll lands mid-run and sees only some of its items. HOLD_MAX_MS: let go even without a run.
const HOLD_MAX_MS = 60_000
let held: { item: InboxItem; since: number }[] = []
const endedFlows = new Set<string>() // runs seen but not yet matched against the Inbox
let baseline = false
let timer: ReturnType<typeof setInterval> | null = null
let polling = false
let deps: PollerDeps | null = null

// Items the capture window created itself: remember them so no notification fires for them.
export function markKnown(id: string): void {
  knownInbox.add(id)
}

// One OS notification for one or more new Inbox items; the click opens the first one.
function notify(title: string, items: InboxItem[]): void {
  if (!Notification.isSupported() || !deps) return
  const first = items[0]
  const kinds = [...new Set(items.map((i) => i.kind))]
  const detail = {
    kind: kinds.length === 1 ? kinds[0] : 'mixed',
    count: items.length,
    items: items.map((i) => i.id)
  }
  const body = items.length > 1 ? `${first.title} 외 ${items.length - 1}건` : first.title
  const n = new Notification({ title, body })
  live.add(n)
  n.on('show', () => postEvent('notification_shown', first.id, detail))
  n.on('click', () => {
    postEvent('notification_clicked', first.id, detail)
    deps?.onClick({ view: 'inbox', id: first.id })
  })
  n.on('close', () => live.delete(n))
  n.show()
}

// New items → notifications. detect/revisit items let go together come from one run, so each kind
// is one notification; capture/pending are one each. Past the limit the rest share one 「외 N건」.
function notifyNew(items: InboxItem[]): void {
  const groups: InboxItem[][] = []
  for (const kind of ['detect', 'revisit'] as const) {
    const g = items.filter((i) => i.kind === kind)
    if (g.length > 0) groups.push(g)
  }
  for (const it of items) if (it.kind === 'capture' || it.kind === 'pending') groups.push([it])
  const shown =
    groups.length > MAX_NOTIFY_PER_POLL ? groups.slice(0, MAX_NOTIFY_PER_POLL - 1) : groups
  for (const g of shown) {
    const title = KIND_TITLE[g[0].kind] + (g.length > 1 ? ` ${g.length}건` : '')
    notify(title, g)
  }
  const rest = groups.slice(shown.length).flat()
  if (rest.length > 0) notify(`새 Inbox 항목 ${rest.length}건`, rest)
}

export async function pollNow(): Promise<void> {
  if (polling || !deps) return
  if (!base()) return
  polling = true
  try {
    // runs before inbox: a run is recorded after its items, so once a run shows, all of its
    // items are already in the Inbox we read next
    const runs = await getJson<{ items: RunResult[] }>('/runs')
    const inbox = await getJson<{ items: InboxItem[] }>('/inbox')
    let changed = false

    if (runs) {
      for (const r of runs.items) {
        if (knownRuns.has(r.id)) continue
        knownRuns.add(r.id)
        changed = true
        if (baseline) endedFlows.add(r.flow)
      }
    }
    // Notifications come only from the Inbox (HYO-111): detect drafts and revisit alerts are
    // Inbox items too, so a run only tells when its held items may go.
    if (inbox) {
      deps.onPending(inbox.items.length)
      const fresh = inbox.items.filter((it) => !knownInbox.has(it.id))
      for (const it of fresh) knownInbox.add(it.id)
      if (fresh.length > 0) changed = true
      if (baseline) {
        const now = Date.now()
        const ready: InboxItem[] = []
        for (const it of fresh) {
          if (it.kind === 'detect' || it.kind === 'revisit') held.push({ item: it, since: now })
          else ready.push(it)
        }
        const go = held.filter((h) => endedFlows.has(h.item.kind) || now - h.since > HOLD_MAX_MS)
        held = held.filter((h) => !go.includes(h))
        endedFlows.clear()
        notifyNew([...go.map((h) => h.item), ...ready])
      }
    }
    if (inbox && runs) baseline = true // the first complete poll is only a baseline
    if (changed) deps.onChanged()
  } finally {
    polling = false
  }
}

export function startPoller(d: PollerDeps): void {
  deps = d
  if (timer) return
  timer = setInterval(() => void pollNow(), POLL_MS)
  void pollNow()
}

export function stopPoller(): void {
  if (timer) clearInterval(timer)
  timer = null
}

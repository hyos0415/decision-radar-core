import { randomUUID } from 'crypto'
import { Notification } from 'electron'
import { getStatus } from './backend'
import type { NavTarget } from '../shared'

// Everything here talks only to the local backend (127.0.0.1) over fetch.

type InboxKind = 'capture' | 'detect' | 'pending' | 'revisit'
type InboxItem = { id: string; kind: InboxKind; title: string }
type RunResult = { id: string }

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
  const body = items.length > 1 ? `${first.title} 외 ${items.length - 1}건` : first.title
  // title · body: the OS text as shown, read back by the past-notifications list (GET /notifications).
  // nid: one id per notification, the same on shown and clicked so the backend pairs them exactly (HYO-113).
  const detail = {
    kind: kinds.length === 1 ? kinds[0] : 'mixed',
    count: items.length,
    items: items.map((i) => i.id),
    title,
    body,
    nid: randomUUID()
  }
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

// One notification per new item. Past the limit the rest share one 「외 N건」 instead of being dropped.
function notifyNew(items: InboxItem[]): void {
  const shown = items.length > MAX_NOTIFY_PER_POLL ? items.slice(0, MAX_NOTIFY_PER_POLL - 1) : items
  for (const it of shown) notify(KIND_TITLE[it.kind], [it])
  const rest = items.slice(shown.length)
  if (rest.length > 0) notify(`새 Inbox 항목 ${rest.length}건`, rest)
}

export async function pollNow(): Promise<void> {
  if (polling || !deps) return
  if (!base()) return
  polling = true
  try {
    const inbox = await getJson<{ items: InboxItem[] }>('/inbox')
    const runs = await getJson<{ items: RunResult[] }>('/runs')
    let changed = false

    // Notifications come only from the Inbox (HYO-111): detect drafts and revisit alerts are
    // Inbox items too, so a new run only refreshes the window.
    if (inbox) {
      deps.onPending(inbox.items.length)
      const fresh = inbox.items.filter((it) => !knownInbox.has(it.id))
      for (const it of fresh) knownInbox.add(it.id)
      if (fresh.length > 0) changed = true
      if (baseline) notifyNew(fresh)
    }
    if (runs) {
      for (const r of runs.items) {
        if (knownRuns.has(r.id)) continue
        knownRuns.add(r.id)
        changed = true
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

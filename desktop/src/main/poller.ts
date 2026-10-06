import { Notification } from 'electron'
import { getStatus } from './backend'
import type { NavTarget } from '../shared'

// Everything here talks only to the local backend (127.0.0.1) over fetch.

type InboxItem = { id: string; title: string }
type RunResult = {
  id: string
  flow: string
  drafts?: { adr: string; title: string }[]
  alerts?: { adr: string }[]
}

type PollerDeps = {
  onClick: (t: NavTarget) => void // show + focus main window and route it
  onPending: (n: number) => void
  onChanged: () => void // data changed: tell the renderer to refresh
}

const POLL_MS = Math.max(1000, Number(process.env.RADAR_POLL_MS) || 5000)
const MAX_NOTIFY_PER_POLL = 3

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
    await fetch(`${b}/runs/${flow}`, {
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

function notify(
  body: string,
  title: string,
  kind: string,
  item: string | undefined,
  target: NavTarget
): void {
  if (!Notification.isSupported() || !deps) return
  const n = new Notification({ title, body })
  live.add(n)
  n.on('show', () => postEvent('notification_shown', item, { kind }))
  n.on('click', () => {
    postEvent('notification_clicked', item, { kind })
    deps?.onClick(target)
  })
  n.on('close', () => live.delete(n))
  n.show()
}

export async function pollNow(): Promise<void> {
  if (polling || !deps) return
  if (!base()) return
  polling = true
  try {
    const inbox = await getJson<{ items: InboxItem[] }>('/inbox')
    const runs = await getJson<{ items: RunResult[] }>('/runs')
    let changed = false
    let left = MAX_NOTIFY_PER_POLL

    if (inbox) {
      deps.onPending(inbox.items.length)
      for (const it of inbox.items) {
        if (knownInbox.has(it.id)) continue
        knownInbox.add(it.id)
        changed = true
        if (baseline && left > 0) {
          left--
          notify(it.title, '승인 대기 새 항목', 'inbox_new', it.id, { view: 'inbox', id: it.id })
        }
      }
    }
    if (runs) {
      // oldest first so notifications come in order
      for (const r of [...runs.items].reverse()) {
        if (knownRuns.has(r.id)) continue
        knownRuns.add(r.id)
        changed = true
        if (!baseline) continue
        const drafts = r.drafts ?? []
        const alerts = r.alerts ?? []
        if (drafts.length > 0 && left > 0) {
          left--
          notify(`detect 초안 ${drafts.length}건`, 'Decision Radar', 'run_drafts', drafts[0].adr, {
            view: 'decisions',
            id: drafts[0].adr
          })
        }
        if (alerts.length > 0 && left > 0) {
          left--
          notify(
            `재검토 조건 충족 ${alerts.length}건`,
            'Decision Radar',
            'run_alerts',
            alerts[0].adr,
            { view: 'decisions', id: alerts[0].adr }
          )
        }
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

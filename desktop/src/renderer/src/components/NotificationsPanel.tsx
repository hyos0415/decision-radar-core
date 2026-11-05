import { useEffect, useRef, useState } from 'react'
import { BellOff } from 'lucide-react'
import { api, type NotificationEntry } from '@renderer/lib/api'
import { dayLabel, timeOnly } from '@renderer/lib/format'
import { cn } from '@renderer/lib/utils'
import type { NavTarget } from '../../../shared'
import { COPY } from '../../../copy'

// Where a row leads (HYO-113): the Inbox item while it waits; once answered, the item a rejudge
// left in the Inbox (new_item — only while it is still there), else the ADR it was answered with.
// A rejected item has no ADR to open — the backend still returns the discarded draft's number.
// A bundled notification (「새 Inbox 항목 N건」) goes to its first item. null → not clickable.
function target(n: NotificationEntry, inboxIds: string[]): NavTarget | null {
  const it = n.items[0]
  if (!it) return null
  if (it.status === 'inbox') return { view: 'inbox', id: it.id }
  if (it.new_item && inboxIds.includes(it.new_item)) return { view: 'inbox', id: it.new_item }
  if (it.answer === 'reject') return null
  return it.adr ? { view: 'decisions', id: it.adr } : null
}

// Past OS notifications, newest first. Read rows (clicked, or the item opened since) are only dimmed —
// no unread count, no bold, no mark-as-read (HYO-110 「알림, 배지로 재촉하지 않는다」).
export function NotificationsPanel({
  inboxIds,
  onNavigate,
  onClose
}: {
  inboxIds: string[]
  onNavigate: (t: NavTarget) => void
  onClose: () => void
}): React.JSX.Element {
  const [list, setList] = useState<NotificationEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLElement>(null)

  useEffect(() => {
    let cancelled = false
    api
      .notifications()
      .then((l) => {
        if (!cancelled) setList(l)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [])

  // focus the panel on open; once the rows arrive, the first clickable one
  useEffect(() => {
    const first = ref.current?.querySelector<HTMLButtonElement>('.notif-row:not(:disabled)')
    ;(first ?? ref.current)?.focus()
  }, [list])

  // A press outside closes it — the bell itself toggles, so it is left out. The Inbox detail panel
  // (and its edit · reject dialogs) is a separate panel: closing or using it leaves this one open.
  // Esc closes it wherever focus is (e.g. on the page after the Inbox panel closed), except
  // inside the Inbox panel or a dialog — those close first. Capture phase on window runs before the
  // Inbox panel's Esc handler, which skips prevented events.
  useEffect(() => {
    const onPointer = (e: PointerEvent): void => {
      const t = e.target
      if (!(t instanceof Element)) return
      if (ref.current?.contains(t)) return
      if (
        t.closest(
          '[data-notifications-toggle], .overlay-panel, [role="dialog"], [role="alertdialog"]'
        )
      )
        return
      onClose()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return
      const t = e.target
      if (t instanceof Element && t.closest('.overlay-panel')) return
      e.preventDefault()
      onClose()
      document.querySelector<HTMLButtonElement>('[data-notifications-toggle]')?.focus()
    }
    window.addEventListener('pointerdown', onPointer)
    window.addEventListener('keydown', onKey, true)
    return () => {
      window.removeEventListener('pointerdown', onPointer)
      window.removeEventListener('keydown', onKey, true)
    }
  }, [onClose])

  let body: React.ReactNode
  if (error) body = <div className="empty-state">{error}</div>
  else if (!list) body = <div className="empty-state">{COPY.term.loading}</div>
  else if (list.length === 0)
    body = (
      <div className="empty-state">
        <BellOff size={20} />
        {COPY.notifications.empty}
      </div>
    )
  else
    body = (
      <div className="notif-items" role="list" aria-label={COPY.notifications.title}>
        {list.map((n) => {
          const t = target(n, inboxIds)
          const read = n.clicked || n.items.some((i) => i.opened)
          return (
            <button
              key={n.id}
              role="listitem"
              className={cn('notif-row', read && 'is-read')}
              disabled={!t}
              title={t ? undefined : COPY.notifications.gone}
              onClick={() => {
                if (t) {
                  onNavigate(t)
                  onClose()
                }
              }}
            >
              <span className="notif-title">
                {n.title ||
                  (n.kind && Object.hasOwn(COPY.notifications.kindTitle, n.kind)
                    ? COPY.notifications.kindTitle[n.kind]
                    : COPY.notifications.fallbackTitle)}
              </span>
              {/* body가 없어도 칸은 남긴다 — 3열 그리드라 빼면 시각이 가운데 칸으로 밀린다 */}
              <span className="notif-body" title={n.body || undefined}>
                {n.body}
              </span>
              <span className="row-meta notif-at">
                {dayLabel(n.at)} {timeOnly(n.at)}
              </span>
            </button>
          )
        })}
      </div>
    )

  return (
    <section ref={ref} tabIndex={-1} className="notif-panel" aria-label={COPY.notifications.title}>
      <header className="notif-header">
        <h2>{COPY.notifications.title}</h2>
        <kbd>Esc</kbd>
      </header>
      {body}
    </section>
  )
}

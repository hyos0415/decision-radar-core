import { useEffect, useRef } from 'react'
import type { InboxItem } from '@renderer/lib/api'
import { firstAffected, STATE_ICON, waitText } from '@renderer/lib/inbox'
import { shortcutKey } from '@renderer/lib/shortcuts'
import { cn } from '@renderer/lib/utils'
import { StatusIcon } from './Status'

export function InboxList({
  items,
  selectedId,
  onSelect
}: {
  items: InboxItem[]
  selectedId: string | null
  onSelect: (id: string) => void
}): React.JSX.Element {
  const rows = useRef<Array<HTMLButtonElement | null>>([])

  // j/k: with an item open, open the next · previous one (same as the panel's ▾ ▴);
  // with none open, move focus between rows like ↓↑
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const k = shortcutKey(e)
      if (k !== 'j' && k !== 'k') return
      e.preventDefault()
      const d = k === 'j' ? 1 : -1
      if (selectedId) {
        const i = items.findIndex((it) => it.id === selectedId)
        const next = i >= 0 ? items[i + d] : undefined
        if (next) onSelect(next.id)
        return
      }
      const cur = rows.current.findIndex((r) => r === document.activeElement)
      rows.current[Math.min(Math.max(cur + d, 0), items.length - 1)]?.focus()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, selectedId, onSelect])

  // ↑↓ moves focus between rows; Enter/Space opens (native button click)
  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, index: number): void {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const next = Math.min(Math.max(index + (e.key === 'ArrowDown' ? 1 : -1), 0), items.length - 1)
    rows.current[next]?.focus()
  }

  return (
    <div className="inbox-items" role="list" aria-label="Inbox 항목">
      {items.map((it, index) => (
        <button
          key={it.id}
          ref={(node) => {
            rows.current[index] = node
          }}
          role="listitem"
          onClick={() => onSelect(it.id)}
          onKeyDown={(e) => onKeyDown(e, index)}
          className={cn('inbox-row', it.id === selectedId && 'selected')}
        >
          <span className="row-icon">
            <StatusIcon status={STATE_ICON[it.state]} />
          </span>
          <span className="row-title" title={it.title}>
            {it.title}
          </span>
          <span className="row-meta mono">{it.technology}</span>
          <span className="row-meta mono">{firstAffected(it)}</span>
          <span className="tag mono">{it.kind}</span>
          <span className="row-meta row-age">{waitText(it.waiting_days)}</span>
        </button>
      ))}
    </div>
  )
}

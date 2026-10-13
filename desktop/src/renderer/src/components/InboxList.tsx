import { useRef } from 'react'
import type { InboxItem } from '@renderer/lib/api'
import { firstAffected, STATE_ICON, waitText } from '@renderer/lib/inbox'
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

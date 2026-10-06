import type { InboxItem, ItemState } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { Badge } from './ui/badge'

export const STATE_LABEL: Partial<Record<ItemState, string>> = {
  stale: '바뀜',
  blocked: '막힘',
  review: '검토'
}

export function waitText(days: number): string {
  return days <= 0 ? '오늘 도착' : `${days}일 대기`
}

export function firstAffected(item: InboxItem): string {
  const a = item.affected[0]
  return a ? `${a.repo}/${a.component}` : ''
}

export function InboxList({
  items,
  selectedId,
  onSelect
}: {
  items: InboxItem[]
  selectedId: string | null
  onSelect: (id: string) => void
}): React.JSX.Element {
  return (
    <div className="flex w-[360px] shrink-0 flex-col border-r">
      <div className="flex h-11 shrink-0 items-center border-b px-4 text-[13px] font-semibold">
        Inbox
      </div>
      <div className="flex-1 overflow-y-auto">
        {items.map((it) => {
          const stateLabel = STATE_LABEL[it.state]
          return (
            <button
              key={it.id}
              onClick={() => onSelect(it.id)}
              className={cn(
                'flex w-full flex-col gap-1 border-b px-4 py-2.5 text-left outline-none hover:bg-accent/60 focus-visible:bg-accent/60',
                it.id === selectedId && 'bg-accent'
              )}
            >
              <div className="truncate text-[13px] font-medium">{it.title}</div>
              <div className="flex items-center gap-2">
                <div className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
                  {it.technology}
                  {firstAffected(it) && ` · ${firstAffected(it)}`}
                </div>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {waitText(it.waiting_days)}
                </span>
              </div>
              <div className="flex items-center gap-1">
                <Badge variant="outline">{it.kind}</Badge>
                {stateLabel && <Badge variant="secondary">{stateLabel}</Badge>}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

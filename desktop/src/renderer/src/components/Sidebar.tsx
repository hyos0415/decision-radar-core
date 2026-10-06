import { Inbox as InboxIcon, Library, Play, Zap } from 'lucide-react'
import { cn } from '@renderer/lib/utils'
import { shortcutText } from '@renderer/lib/format'
import type { RadarInfo } from '../../../shared'

export type View = 'inbox' | 'decisions' | 'runs'

function NavItem({
  active,
  icon,
  label,
  right,
  onClick
}: {
  active: boolean
  icon: React.ReactNode
  label: string
  right?: React.ReactNode
  onClick: () => void
}): React.JSX.Element {
  return (
    <button
      onClick={onClick}
      className={cn(
        'flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] outline-none hover:bg-accent/60 focus-visible:bg-accent/60',
        active ? 'bg-accent font-medium' : 'text-foreground/80'
      )}
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className="flex-1">{label}</span>
      {right}
    </button>
  )
}

export function Sidebar({
  view,
  onView,
  pending,
  stub,
  info
}: {
  view: View
  onView: (v: View) => void
  pending: number
  stub: boolean
  info: RadarInfo | null
}): React.JSX.Element {
  return (
    <aside className="flex w-52 shrink-0 flex-col border-r bg-muted/60">
      <div className="px-4 pt-4 pb-3 text-[13px] font-semibold tracking-tight">Decision Radar</div>
      <nav className="flex flex-col gap-0.5 px-2">
        <NavItem
          active={view === 'inbox'}
          icon={<InboxIcon className="size-4" />}
          label="Inbox"
          right={<span className="font-mono text-xs text-muted-foreground">{pending}</span>}
          onClick={() => onView('inbox')}
        />
        <NavItem
          active={view === 'decisions'}
          icon={<Library className="size-4" />}
          label="Decisions"
          onClick={() => onView('decisions')}
        />
        <NavItem
          active={view === 'runs'}
          icon={<Play className="size-4" />}
          label="실행"
          onClick={() => onView('runs')}
        />
      </nav>
      <div className="mt-auto flex flex-col gap-2 px-2 pb-3">
        <button
          onClick={() => void window.radar.openCapture()}
          className="flex h-8 items-center gap-2 rounded-md border bg-background px-2 text-[13px] outline-none hover:bg-accent focus-visible:bg-accent"
        >
          <Zap className="size-4 text-muted-foreground" />
          <span className="flex-1 text-left whitespace-nowrap">빠른 capture</span>
        </button>
        {info && (
          <div className="px-1 font-mono text-[10px] text-muted-foreground">
            {info.shortcutOk ? shortcutText(info.shortcut) : '단축키 등록 실패'}
          </div>
        )}
        {stub && (
          <div className="px-1 text-[11px] text-muted-foreground">
            <span className="rounded-md border px-1.5 py-0.5">스텁</span>
          </div>
        )}
      </div>
    </aside>
  )
}

import { Inbox as InboxIcon, Library } from 'lucide-react'

export function Sidebar({ pending }: { pending: number }): React.JSX.Element {
  return (
    <aside className="flex w-52 shrink-0 flex-col border-r bg-muted/60">
      <div className="px-4 pt-4 pb-3 text-[13px] font-semibold tracking-tight">Decision Radar</div>
      <nav className="flex flex-col gap-0.5 px-2">
        <div className="flex h-8 items-center gap-2 rounded-md bg-accent px-2 text-[13px] font-medium">
          <InboxIcon className="size-4 text-muted-foreground" />
          <span className="flex-1">Inbox</span>
          <span className="font-mono text-xs text-muted-foreground">{pending}</span>
        </div>
        <div
          aria-disabled
          className="flex h-8 cursor-not-allowed items-center gap-2 rounded-md px-2 text-[13px] text-muted-foreground/70"
        >
          <Library className="size-4" />
          <span className="flex-1">Decisions</span>
          <span className="text-[11px]">곧</span>
        </div>
      </nav>
    </aside>
  )
}

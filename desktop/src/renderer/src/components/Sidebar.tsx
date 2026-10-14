import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Inbox as InboxIcon,
  Library,
  Loader2,
  Monitor,
  Moon,
  Play,
  ScrollText,
  Sun,
  Zap
} from 'lucide-react'
import type { Flow, Job } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { shortcutText, timeOnly } from '@renderer/lib/format'
import type { RadarInfo, ThemeSource } from '../../../shared'
import { COPY } from '../../../copy'

export type View = 'inbox' | 'decisions' | 'runs'

export type RunState = {
  active: Job | null // detect · revisit 작업이 접수됐거나 도는 중
  lastFailed: boolean // 가장 최근 실행이 실패
  nextSlot: string | null // 정기 실행 다음 시각 (ISO)
}

const FLOW_HINT: Record<Flow, string> = COPY.sidebar.flowHint

function RunStatusText({ run }: { run: RunState }): React.JSX.Element | null {
  if (run.active)
    return (
      <span className="run-row-status">
        <Loader2 className="spin" size={12} />
        <span className="mono">{run.active.kind}</span> {COPY.sidebar.running}
      </span>
    )
  if (run.lastFailed)
    return (
      <span className="run-row-status">
        <AlertTriangle className="tone-warning" size={13} />
        {COPY.sidebar.failed}
      </span>
    )
  if (run.nextSlot)
    return <span className="run-row-status">{COPY.sidebar.next(timeOnly(run.nextSlot, true))}</span>
  return null
}

function RunMenu({
  run,
  flows,
  stub,
  onRun,
  onClose
}: {
  run: RunState
  flows: Flow[]
  stub: boolean
  onRun: (flow: Flow) => void
  onClose: () => void
}): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('.run-menu-item:not(:disabled)')?.focus()
  }, [])

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>): void {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
      return
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
    e.preventDefault()
    const items = Array.from(
      ref.current?.querySelectorAll<HTMLButtonElement>('.run-menu-item:not(:disabled)') ?? []
    )
    if (items.length === 0) return
    const at = items.indexOf(document.activeElement as HTMLButtonElement)
    const next = (at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next]?.focus()
  }

  return (
    <div className="run-menu" role="menu" ref={ref} onKeyDown={onKeyDown}>
      {(['revisit', 'detect'] as const).map((flow) => {
        const available = flows.includes(flow)
        return (
          <button
            key={flow}
            className="run-menu-item"
            role="menuitem"
            disabled={!available || run.active !== null}
            onClick={() => onRun(flow)}
          >
            <span>
              <strong>
                <span className="mono">{flow}</span>
              </strong>
              <small>{available ? FLOW_HINT[flow] : COPY.sidebar.unavailable}</small>
            </span>
            {run.active?.kind === flow && <Loader2 className="spin" size={13} />}
          </button>
        )
      })}
      {stub && (
        <>
          <div className="run-menu-separator" />
          <span className="run-menu-label">{COPY.sidebar.stubNote}</span>
        </>
      )}
    </div>
  )
}

const THEMES: { value: ThemeSource; label: string; icon: React.ReactNode }[] = [
  { value: 'system', label: COPY.sidebar.themes.system, icon: <Monitor size={13} /> },
  { value: 'light', label: COPY.sidebar.themes.light, icon: <Sun size={13} /> },
  { value: 'dark', label: COPY.sidebar.themes.dark, icon: <Moon size={13} /> }
]

// main switches nativeTheme.themeSource — main.css tokens and the window colors follow (HYO-104)
function ThemeSwitch(): React.JSX.Element {
  const [theme, setTheme] = useState<ThemeSource | null>(null)

  useEffect(() => {
    void window.radar.getTheme().then(setTheme)
  }, [])

  function choose(t: ThemeSource): void {
    setTheme(t)
    void window.radar.setTheme(t)
  }

  return (
    <div className="sidebar-environment">
      <span>{COPY.sidebar.theme}</span>
      <div className="segmented segmented-sm" role="group" aria-label={COPY.sidebar.theme}>
        {THEMES.map((t) => (
          <button
            key={t.value}
            aria-pressed={theme === t.value}
            aria-label={t.label}
            title={t.label}
            className={cn(theme === t.value && 'active')}
            onClick={() => choose(t.value)}
          >
            {t.icon}
          </button>
        ))}
      </div>
    </div>
  )
}

function NavItem({
  active,
  icon,
  label,
  count,
  onClick
}: {
  active: boolean
  icon: React.ReactNode
  label: string
  count?: number
  onClick: () => void
}): React.JSX.Element {
  return (
    <button onClick={onClick} className={cn('nav-item', active && 'active')}>
      {icon}
      <span>{label}</span>
      {count !== undefined && <span className="count">{count}</span>}
    </button>
  )
}

export function Sidebar({
  view,
  onView,
  pending,
  stub,
  profile,
  info,
  run,
  flows,
  onRun
}: {
  view: View
  onView: (v: View) => void
  pending: number
  stub: boolean
  profile?: string | null
  info: RadarInfo | null
  run: RunState
  flows: Flow[]
  onRun: (flow: Flow) => void
}): React.JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false)
  const card = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const onPointer = (e: PointerEvent): void => {
      if (e.target instanceof Node && !card.current?.contains(e.target)) setMenuOpen(false)
    }
    window.addEventListener('pointerdown', onPointer)
    return () => window.removeEventListener('pointerdown', onPointer)
  }, [menuOpen])

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <button className="wordmark" onClick={() => onView('inbox')}>
          Decision Radar
        </button>
      </div>
      <nav className="nav">
        <NavItem
          active={view === 'inbox'}
          icon={<InboxIcon size={16} />}
          label={COPY.term.inbox}
          count={pending}
          onClick={() => onView('inbox')}
        />
        <NavItem
          active={view === 'decisions'}
          icon={<Library size={16} />}
          label={COPY.term.decisions}
          onClick={() => onView('decisions')}
        />
        <NavItem
          active={view === 'runs'}
          icon={<ScrollText size={16} />}
          label={COPY.term.runs}
          onClick={() => onView('runs')}
        />
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-action-card" ref={card}>
          <button
            className="sidebar-action"
            onClick={() => void window.radar.openCapture()}
            title={info?.shortcutOk ? shortcutText(info.shortcut) : undefined}
          >
            <Zap size={15} />
            <span>{COPY.term.capture}</span>
            {info?.shortcutOk && <kbd>{shortcutText(info.shortcut, true)}</kbd>}
          </button>
          <button
            className={cn('sidebar-action', menuOpen && 'active')}
            onClick={() => setMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            <Play size={15} />
            <span>{COPY.sidebar.run}</span>
            <RunStatusText run={run} />
          </button>
          {menuOpen && (
            <RunMenu
              run={run}
              flows={flows}
              stub={stub}
              onClose={() => setMenuOpen(false)}
              onRun={(flow) => {
                setMenuOpen(false)
                onRun(flow)
              }}
            />
          )}
        </div>
        <ThemeSwitch />
        {info && !info.shortcutOk && (
          <div className="sidebar-environment">{COPY.sidebar.shortcutFailed}</div>
        )}
        {stub && (
          <div className="sidebar-environment">
            <span className="tag mono">
              {profile && profile !== 'default'
                ? COPY.sidebar.stubProfile(profile)
                : COPY.sidebar.stub}
            </span>
            <span>{COPY.sidebar.noModel}</span>
          </div>
        )}
      </div>
    </aside>
  )
}

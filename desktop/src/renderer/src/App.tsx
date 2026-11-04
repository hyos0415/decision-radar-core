import { useCallback, useEffect, useRef, useState } from 'react'
import { Inbox as InboxIcon } from 'lucide-react'
import { toast, Toaster } from 'sonner'
import {
  api,
  setBaseUrl,
  type Flow,
  type InboxDetail,
  type InboxItem,
  type Job,
  type RunResult,
  type Schedule
} from './lib/api'
import { InboxList } from './components/InboxList'
import { ItemDetail } from './components/ItemDetail'
import { DecisionsView } from './components/DecisionsView'
import { TopicsView } from './components/TopicsView'
import { WeekListView } from './components/WeekListView'
import { RunsView } from './components/RunsView'
import { NotificationsPanel } from './components/NotificationsPanel'
import { PageHeader } from './components/Status'
import { Sidebar, type RunState, type View } from './components/Sidebar'
import type { NavTarget, RadarInfo } from '../../shared'
import { COPY } from '../../copy'

type Backend = Awaited<ReturnType<typeof window.radar.getBackend>>

const JOB_POLL_MS = 1500

function Centered({ children }: { children: React.ReactNode }): React.JSX.Element {
  return <div className="empty-state h-full">{children}</div>
}

function isActive(j: Job): boolean {
  return j.kind !== 'capture' && (j.state === 'queued' || j.state === 'running')
}

function App(): React.JSX.Element {
  const [backend, setBackend] = useState<Backend>({ status: 'starting' })
  const [items, setItems] = useState<InboxItem[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<InboxDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [view, setView] = useState<View>('inbox')
  const [decisionId, setDecisionId] = useState<string | null>(null)
  const [tick, setTick] = useState(0)
  const [mode, setMode] = useState<string | null>(null)
  const [profile, setProfile] = useState<string | null>(null)
  const [flows, setFlows] = useState<Flow[]>([])
  const [info, setInfo] = useState<RadarInfo | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [runs, setRuns] = useState<RunResult[] | null>(null)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [schedule, setSchedule] = useState<Schedule | null>(null)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const sentOpened = useRef(false)
  const selectedRef = useRef<string | null>(null)
  const panelRef = useRef<HTMLElement | null>(null)
  // set when an item is answered: once the next detail is in, focus moves to the panel
  const focusNext = useRef(false)

  useEffect(() => {
    selectedRef.current = selectedId
  }, [selectedId])

  useEffect(() => {
    // the detail pane is cleared whenever the backend is not ready
    const apply = (b: Backend): void => {
      setBackend(b)
      if (!(b.status === 'ready' && !!b.baseUrl)) setDetail(null)
    }
    window.radar.getBackend().then(apply)
    return window.radar.onBackendStatus(apply)
  }, [])

  const ready = backend.status === 'ready' && !!backend.baseUrl
  if (ready) setBaseUrl(backend.baseUrl!)

  // preferIndex: after answering, open the item that took its place (closes when the list is empty).
  // preferId: open this id if present. Otherwise the open item stays open while it still exists.
  const refresh = useCallback(
    (preferIndex?: number, preferId?: string): Promise<void> =>
      api
        .inbox()
        .then((list) => {
          setItems(list)
          setLoadError(null)
          const cur = selectedRef.current
          if (preferId && list.some((i) => i.id === preferId)) {
            setSelectedId(preferId)
          } else if (preferIndex !== undefined) {
            const next = list[Math.min(preferIndex, list.length - 1)]
            setSelectedId(next ? next.id : null)
            if (!next) setDetail(null)
          } else if (cur && !list.some((i) => i.id === cur)) {
            setSelectedId(null)
            setDetail(null)
          }
        })
        .catch((e) => {
          setLoadError(e instanceof Error ? e.message : String(e))
        }),
    []
  )

  // runs + schedule feed both the sidebar run row and the runs view (newest run first)
  const refreshRunInfo = useCallback((): void => {
    api
      .runs()
      .then((r) => {
        setRuns([...r].sort((a, b) => b.at.localeCompare(a.at)))
        setRunsError(null)
      })
      .catch((e) => setRunsError(e instanceof Error ? e.message : String(e)))
    api
      .schedule()
      .then(setSchedule)
      .catch(() => setSchedule(null))
  }, [])

  // null when /jobs failed — the caller must not read that as "the job ended"
  const loadJobs = useCallback(
    (): Promise<Job[] | null> =>
      api
        .jobs()
        .then((list) => {
          setJobs(list)
          return list
        })
        .catch(() => null),
    []
  )

  useEffect(() => {
    window.radar.getInfo().then((i) => {
      setInfo(i)
      document.documentElement.classList.add(`platform-${i.platform}`)
    })
  }, [])

  useEffect(() => {
    if (!ready) return
    api
      .health()
      .then((h) => {
        setMode(h.mode)
        setProfile(h.profile)
        setFlows(h.flows)
      })
      .catch(() => {})
  }, [ready])

  // notification click, capture window, past-notifications list (HYO-113)
  const navigate = useCallback(
    (t: NavTarget): void => {
      setView(t.view)
      if (t.view === 'inbox') {
        if (t.id) setSelectedId(t.id)
        void refresh(undefined, t.id)
      } else if (t.view === 'decisions') {
        setDecisionId(t.id ?? null)
      }
      setTick((n) => n + 1)
    },
    [refresh]
  )
  const closeNotifications = useCallback(() => setNotificationsOpen(false), [])

  // main -> renderer: navigate and data-changed pushes
  useEffect(() => {
    const offNav = window.radar.onNavigate(navigate)
    const offData = window.radar.onDataChanged(() => {
      void refresh()
      refreshRunInfo()
      void loadJobs()
      setTick((n) => n + 1)
    })
    return () => {
      offNav()
      offData()
    }
  }, [navigate, refresh, refreshRunInfo, loadJobs])

  useEffect(() => {
    if (!ready) return
    if (!sentOpened.current) {
      sentOpened.current = true
      api.event('app_opened')
    }
    void refresh()
    refreshRunInfo()
    void loadJobs()
    const onFocus = (): void => {
      void refresh()
      refreshRunInfo()
      setTick((n) => n + 1)
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [ready, refresh, refreshRunInfo, loadJobs])

  // while a detect · revisit job is queued or running, poll until it ends, then reload everything
  const activeJobs = jobs.filter(isActive)
  const activeJob = activeJobs[0] ?? null
  const activeId = activeJob?.id ?? null
  useEffect(() => {
    if (!ready || !activeId) return
    const timer = setInterval(() => {
      void loadJobs().then((list) => {
        if (!list) return // backend did not answer — try again next tick
        const j = list.find((x) => x.id === activeId)
        if (j && isActive(j)) return
        if (j?.state === 'error') toast(j.error ?? COPY.app.jobFailed(j.kind))
        void refresh()
        refreshRunInfo()
        setTick((n) => n + 1)
      })
    }, JOB_POLL_MS)
    return () => clearInterval(timer)
  }, [ready, activeId, loadJobs, refresh, refreshRunInfo])

  useEffect(() => {
    if (ready && view === 'runs') refreshRunInfo()
  }, [ready, view, refreshRunInfo])

  async function startRun(flow: Flow): Promise<void> {
    try {
      await api.submitJob(flow)
      await loadJobs()
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    if (!ready || !selectedId) return
    let cancelled = false
    api
      .detail(selectedId)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
        api.event('item_opened', selectedId)
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [ready, selectedId])

  const closeItem = useCallback((): void => {
    setSelectedId(null)
    setDetail(null)
  }, [])

  // Esc closes the Inbox panel — not while the edit · reject dialog is open (it closes first)
  useEffect(() => {
    if (view !== 'inbox' || !selectedId) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== 'Escape' || e.defaultPrevented) return
      if (document.querySelector('[role="dialog"], [role="alertdialog"]')) return
      closeItem()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [view, selectedId, closeItem])

  function onResolved(): void {
    // the answered detail stays (inert) until the next one loads; refresh clears it if none is left
    const idx = items ? items.findIndex((i) => i.id === selectedId) : 0
    focusNext.current = true
    void refresh(Math.max(idx, 0))
  }

  // the panel is inert until the next detail arrives, so focus it only then
  useEffect(() => {
    if (!focusNext.current || !detail || detail.id !== selectedId) return
    focusNext.current = false
    panelRef.current?.focus()
  }, [detail, selectedId])

  const index = items && selectedId ? items.findIndex((i) => i.id === selectedId) : -1
  const step = (d: -1 | 1): (() => void) | null => {
    const next = items?.[index + d]
    return index >= 0 && next ? () => setSelectedId(next.id) : null
  }

  let body: React.ReactNode
  if (ready && view === 'decisions') {
    body = <DecisionsView selectedId={decisionId} onSelect={setDecisionId} refreshKey={tick} />
  } else if (ready && view === 'weekList') {
    body = <WeekListView refreshKey={tick} />
  } else if (ready && view === 'topics') {
    body = <TopicsView refreshKey={tick} />
  } else if (ready && view === 'runs') {
    body = (
      <RunsView
        runs={runs}
        error={runsError}
        schedule={schedule}
        active={activeJobs}
        onOpenDecision={(id) => {
          setDecisionId(id)
          setView('decisions')
        }}
      />
    )
  } else if (backend.status === 'failed') {
    body = (
      <Centered>
        <div className="max-w-xl">
          <div className="mb-2 text-foreground">{COPY.app.backendFailed}</div>
          <div className="mb-3">{backend.error}</div>
          {backend.logs && backend.logs.length > 0 && (
            <pre className="max-h-64 overflow-auto rounded-md border bg-muted p-3 text-left font-mono text-[11px] whitespace-pre-wrap">
              {backend.logs.slice(-20).join('\n')}
            </pre>
          )}
        </div>
      </Centered>
    )
  } else if (!ready) {
    body = <Centered>{COPY.app.backendStarting}</Centered>
  } else {
    // The panel stays mounted while moving between items (no slide-in replay); until the next
    // detail arrives the previous one stays visible but inert, so nothing can act on it.
    const open = !!selectedId
    const loading = !detail || detail.id !== selectedId
    body = (
      <div className={`inbox-workspace ${open ? 'is-open' : ''}`}>
        <PageHeader title={COPY.term.inbox} count={items?.length} />
        {loadError && !items ? (
          <Centered>{loadError}</Centered>
        ) : !items ? (
          <Centered>{COPY.term.loading}</Centered>
        ) : items.length === 0 ? (
          <Centered>
            <InboxIcon size={22} />
            <strong>{COPY.inbox.empty}</strong>
          </Centered>
        ) : (
          <InboxList
            items={items}
            selectedId={selectedId}
            onSelect={(id) => (id === selectedId ? closeItem() : setSelectedId(id))}
          />
        )}
        {open && (
          <section
            ref={panelRef}
            tabIndex={-1}
            className="overlay-panel"
            aria-label={COPY.inbox.detailLabel}
            aria-busy={loading}
            inert={loading}
          >
            {detail ? (
              <ItemDetail
                key={detail.id}
                detail={detail}
                onResolved={onResolved}
                onClose={closeItem}
                onPrev={step(-1)}
                onNext={step(1)}
                active={!loading}
              />
            ) : (
              <div className="empty-state">{COPY.term.loading}</div>
            )}
          </section>
        )}
      </div>
    )
  }

  return (
    <div className="desktop-window">
      <Sidebar
        view={view}
        onView={setView}
        pending={items?.length ?? 0}
        stub={mode === 'stub'}
        profile={profile}
        info={info}
        run={
          {
            active: activeJob,
            lastFailed: !!runs?.[0] && !runs[0].ok,
            nextSlot: schedule?.enabled
              ? (schedule.flows.map((f) => f.next_slot).sort()[0] ?? null)
              : null
          } satisfies RunState
        }
        flows={flows}
        onRun={(flow) => void startRun(flow)}
        notificationsOpen={notificationsOpen}
        onNotifications={() => setNotificationsOpen((o) => !o)}
      />
      <main className="main">
        <div className="view-area">{body}</div>
        {ready && notificationsOpen && (
          <NotificationsPanel
            inboxIds={items?.map((i) => i.id) ?? []}
            onNavigate={navigate}
            onClose={closeNotifications}
          />
        )}
      </main>
      <Toaster theme="system" position="bottom-right" />
    </div>
  )
}

export default App

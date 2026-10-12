import { useCallback, useEffect, useRef, useState } from 'react'
import { Inbox as InboxIcon } from 'lucide-react'
import { toast, Toaster } from 'sonner'
import { api, setBaseUrl, type Flow, type InboxDetail, type InboxItem, type Job } from './lib/api'
import { InboxList } from './components/InboxList'
import { ItemDetail } from './components/ItemDetail'
import { DecisionsView } from './components/DecisionsView'
import { RunsView } from './components/RunsView'
import { PageHeader } from './components/Status'
import { Sidebar, type RunState, type View } from './components/Sidebar'
import type { NavTarget, RadarInfo } from '../../shared'

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
  const [lastFailed, setLastFailed] = useState(false)
  const [nextSlot, setNextSlot] = useState<string | null>(null)
  const sentOpened = useRef(false)
  const selectedRef = useRef<string | null>(null)

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

  const refreshRunInfo = useCallback((): void => {
    api
      .runs()
      .then((runs) => {
        const latest = [...runs].sort((a, b) => b.at.localeCompare(a.at))[0]
        setLastFailed(!!latest && !latest.ok)
      })
      .catch(() => {})
    api
      .schedule()
      .then((s) => {
        const slots = s.enabled ? s.flows.map((f) => f.next_slot).sort() : []
        setNextSlot(slots[0] ?? null)
      })
      .catch(() => setNextSlot(null))
  }, [])

  const loadJobs = useCallback(
    (): Promise<Job[]> =>
      api
        .jobs()
        .then((list) => {
          setJobs(list)
          return list
        })
        .catch(() => []),
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

  // main -> renderer: navigate (notification click, capture window) and data-changed pushes
  useEffect(() => {
    const offNav = window.radar.onNavigate((t: NavTarget) => {
      setView(t.view)
      if (t.view === 'inbox') {
        if (t.id) setSelectedId(t.id)
        void refresh(undefined, t.id)
      } else if (t.view === 'decisions') {
        setDecisionId(t.id ?? null)
      }
      setTick((n) => n + 1)
    })
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
  }, [refresh, refreshRunInfo, loadJobs])

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
  const activeJob = jobs.find(isActive) ?? null
  useEffect(() => {
    if (!ready || !activeJob) return
    const id = activeJob.id
    const timer = setInterval(() => {
      void loadJobs().then((list) => {
        const j = list.find((x) => x.id === id)
        if (j && isActive(j)) return
        if (j?.state === 'error') toast(j.error ?? `${j.kind} 실행이 실패했다.`)
        void refresh()
        refreshRunInfo()
        setTick((n) => n + 1)
      })
    }, JOB_POLL_MS)
    return () => clearInterval(timer)
  }, [ready, activeJob, loadJobs, refresh, refreshRunInfo])

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
    const idx = items ? items.findIndex((i) => i.id === selectedId) : 0
    setDetail(null)
    void refresh(Math.max(idx, 0))
  }

  const index = items && selectedId ? items.findIndex((i) => i.id === selectedId) : -1
  const step = (d: -1 | 1): (() => void) | null => {
    const next = items?.[index + d]
    return index >= 0 && next ? () => setSelectedId(next.id) : null
  }

  let body: React.ReactNode
  if (ready && view === 'decisions') {
    body = <DecisionsView selectedId={decisionId} onSelect={setDecisionId} refreshKey={tick} />
  } else if (ready && view === 'runs') {
    body = (
      <RunsView
        refreshKey={tick}
        jobs={jobs}
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
          <div className="mb-2 text-foreground">백엔드를 시작하지 못했다.</div>
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
    body = <Centered>백엔드 기동 중…</Centered>
  } else {
    const open = !!(detail && detail.id === selectedId)
    body = (
      <div className={`inbox-workspace ${open ? 'is-open' : ''}`}>
        <PageHeader title="Inbox" count={items?.length} />
        {loadError && !items ? (
          <Centered>{loadError}</Centered>
        ) : !items ? (
          <Centered>불러오는 중…</Centered>
        ) : items.length === 0 ? (
          <Centered>
            <InboxIcon size={22} />
            <strong>대기 중인 항목이 없다</strong>
          </Centered>
        ) : (
          <InboxList
            items={items}
            selectedId={selectedId}
            onSelect={(id) => (id === selectedId ? closeItem() : setSelectedId(id))}
          />
        )}
        {open && detail && (
          <ItemDetail
            key={detail.id}
            detail={detail}
            onResolved={onResolved}
            onClose={closeItem}
            onPrev={step(-1)}
            onNext={step(1)}
          />
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
        run={{ active: activeJob, lastFailed, nextSlot } satisfies RunState}
        flows={flows}
        onRun={(flow) => void startRun(flow)}
      />
      <main className="main">
        <div className="view-area">{body}</div>
      </main>
      <Toaster theme="system" position="bottom-right" />
    </div>
  )
}

export default App

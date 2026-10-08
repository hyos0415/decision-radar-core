import { useCallback, useEffect, useRef, useState } from 'react'
import { Toaster } from 'sonner'
import { api, setBaseUrl, type InboxDetail, type InboxItem } from './lib/api'
import { InboxList } from './components/InboxList'
import { ItemDetail } from './components/ItemDetail'
import { DecisionsView } from './components/DecisionsView'
import { RunsView } from './components/RunsView'
import { Sidebar, type View } from './components/Sidebar'
import type { NavTarget, RadarInfo } from '../../shared'

type Backend = Awaited<ReturnType<typeof window.radar.getBackend>>

function Centered({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="flex h-full flex-1 items-center justify-center p-8 text-center text-[13px] text-muted-foreground">
      {children}
    </div>
  )
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
  const [info, setInfo] = useState<RadarInfo | null>(null)
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

  // preferred: id to select if still present; otherwise keep current, else first
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
          } else if (!cur || !list.some((i) => i.id === cur)) {
            setSelectedId(list[0]?.id ?? null)
            if (!list[0]) setDetail(null)
          }
        })
        .catch((e) => {
          setLoadError(e instanceof Error ? e.message : String(e))
        }),
    []
  )

  useEffect(() => {
    window.radar.getInfo().then(setInfo)
  }, [])

  useEffect(() => {
    if (!ready) return
    api
      .health()
      .then((h) => {
        setMode(h.mode)
        setProfile(h.profile)
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
      setTick((n) => n + 1)
    })
    return () => {
      offNav()
      offData()
    }
  }, [refresh])

  useEffect(() => {
    if (!ready) return
    if (!sentOpened.current) {
      sentOpened.current = true
      api.event('app_opened')
    }
    void refresh()
    const onFocus = (): void => {
      void refresh()
      setTick((n) => n + 1)
    }
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [ready, refresh])

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

  function onResolved(): void {
    const idx = items ? items.findIndex((i) => i.id === selectedId) : 0
    setDetail(null)
    void refresh(Math.max(idx, 0))
  }

  let body: React.ReactNode
  if (ready && view === 'decisions') {
    body = <DecisionsView selectedId={decisionId} onSelect={setDecisionId} refreshKey={tick} />
  } else if (ready && view === 'runs') {
    body = (
      <RunsView
        refreshKey={tick}
        onOpenDecision={(id) => {
          setDecisionId(id)
          setView('decisions')
        }}
        onChanged={() => void refresh()}
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
  } else if (loadError && !items) {
    body = <Centered>{loadError}</Centered>
  } else if (!items) {
    body = <Centered>불러오는 중…</Centered>
  } else if (items.length === 0) {
    body = <Centered>대기 중인 항목이 없다</Centered>
  } else {
    body = (
      <>
        <InboxList items={items} selectedId={selectedId} onSelect={setSelectedId} />
        {detail && detail.id === selectedId ? (
          <ItemDetail key={detail.id} detail={detail} onResolved={onResolved} />
        ) : (
          <Centered>{loadError ?? '항목을 고르면 여기에 보인다'}</Centered>
        )}
      </>
    )
  }

  return (
    <div className="flex h-full">
      <Sidebar
        view={view}
        onView={setView}
        pending={items?.length ?? 0}
        stub={mode === 'stub'}
        profile={profile}
        info={info}
      />
      <main className="flex min-w-0 flex-1">{body}</main>
      <Toaster theme="light" position="bottom-right" />
    </div>
  )
}

export default App

import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { api, type Decision, type DecisionDetail } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { dateOnly, resultLabel, revisitParts, statusLabel } from '@renderer/lib/format'
import { Badge } from './ui/badge'
import { Button } from './ui/button'
import { Input } from './ui/input'

const COLS = 'grid-cols-[44px_minmax(0,1fr)_84px_52px_120px_76px]'
const FILTERS: { key: string; label: string }[] = [
  { key: 'all', label: '전체' },
  { key: 'proposed', label: '제안' },
  { key: 'adopted', label: '채택' },
  { key: 'hold', label: '보류' },
  { key: 'rejected', label: '기각' }
]

function Revisit({ d }: { d: Decision }): React.JSX.Element {
  const parts = revisitParts(d.revisit_summary)
  return (
    <span className="truncate text-xs text-muted-foreground">
      {parts.map((p, i) => (
        <span key={i} className={cn(p.strong && 'font-semibold text-foreground')}>
          {i > 0 && ' · '}
          {p.text}
        </span>
      ))}
    </span>
  )
}

function Panel({
  id,
  onClose
}: {
  id: string
  onClose: () => void
}): React.JSX.Element {
  const [d, setD] = useState<DecisionDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setD(null)
    setErr(null)
    api
      .decision(id)
      .then((r) => {
        if (cancelled) return
        setD(r)
        api.event('decision_opened', id)
      })
      .catch((e) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [id])

  return (
    <div className="flex h-full w-[380px] shrink-0 flex-col border-l">
      <div className="flex h-11 shrink-0 items-center justify-between border-b px-4">
        <span className="font-mono text-xs text-muted-foreground">{id}</span>
        <Button variant="ghost" size="icon" className="size-7" onClick={onClose} aria-label="닫기">
          <X className="size-4" />
        </Button>
      </div>
      {err && <div className="p-4 text-[13px] text-muted-foreground">{err}</div>}
      {!d && !err && <div className="p-4 text-[13px] text-muted-foreground">불러오는 중…</div>}
      {d && (
        <div className="flex-1 overflow-y-auto px-5 py-4">
          <h2 className="text-base leading-snug font-semibold">{d.title}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
            <Badge variant="secondary">{statusLabel(d.status)}</Badge>
            <span className="font-mono">{d.technology}</span>
            <span>·</span>
            <span>{d.use}</span>
            <span>·</span>
            <span>{dateOnly(d.date)}</span>
            {d.affected.length > 0 && (
              <>
                <span>·</span>
                <span className="font-mono">
                  {d.affected.map((a) => `${a.repo}/${a.component}`).join(', ')}
                </span>
              </>
            )}
          </div>

          <div className="mt-5 mb-1.5 text-xs font-medium">재검토 조건</div>
          {d.revisit.length === 0 ? (
            <div className="text-xs text-muted-foreground">조건이 없다</div>
          ) : (
            <ul className="divide-y rounded-md border">
              {d.revisit.map((r) => (
                <li key={r.id} className="px-3 py-2">
                  <div className="text-[13px]">{r.condition}</div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
                    <Badge variant="outline">{r.check_type}</Badge>
                    <span className={cn(r.last?.result === 'met' && 'font-semibold text-foreground')}>
                      {resultLabel(r.last?.result)}
                    </span>
                    {r.last && <span>{dateOnly(r.last.checked_at)}</span>}
                    {r.last?.reason && <span className="truncate">{r.last.reason}</span>}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-5 mb-1.5 flex flex-wrap items-baseline justify-between gap-x-3">
            <span className="font-mono text-xs">{d.file}</span>
            <span className="text-[11px] text-muted-foreground">상태는 ADR 파일에서 사람이 바꾼다</span>
          </div>
          <pre className="overflow-x-auto rounded-md border bg-muted p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap">
            {d.markdown}
          </pre>
        </div>
      )}
    </div>
  )
}

export function DecisionsView({
  selectedId,
  onSelect,
  refreshKey
}: {
  selectedId: string | null
  onSelect: (id: string | null) => void
  refreshKey: number
}): React.JSX.Element {
  const [items, setItems] = useState<Decision[] | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('all')

  useEffect(() => {
    api
      .decisions()
      .then((r) => {
        setItems(r)
        setErr(null)
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [refreshKey])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (items ?? []).filter(
      (d) =>
        (status === 'all' || d.status === status || (status === 'adopted' && d.status === 'accepted')) &&
        (!needle ||
          `${d.id} ${d.title} ${d.technology}`.toLowerCase().includes(needle))
    )
  }, [items, q, status])

  return (
    <div className="flex min-w-0 flex-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-11 shrink-0 items-center gap-3 border-b px-4">
          <span className="shrink-0 text-[13px] font-semibold">Decisions</span>
          <div className="flex shrink-0 items-center gap-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setStatus(f.key)}
                className={cn(
                  'shrink-0 whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] outline-none hover:bg-accent focus-visible:bg-accent',
                  status === f.key ? 'bg-accent font-medium' : 'text-muted-foreground'
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="번호·제목·기술 검색"
            className="ml-auto h-7 max-w-56"
          />
        </div>
        {err && !items ? (
          <div className="p-6 text-[13px] text-muted-foreground">{err}</div>
        ) : !items ? (
          <div className="p-6 text-[13px] text-muted-foreground">불러오는 중…</div>
        ) : (
          <div className="flex-1 overflow-auto">
            <div
              className={cn(
                'sticky top-0 z-10 grid h-8 items-center gap-2 border-b bg-muted px-4 whitespace-nowrap text-[11px] font-medium text-muted-foreground',
                COLS
              )}
            >
              <span>번호</span>
              <span>제목</span>
              <span>기술</span>
              <span>상태</span>
              <span>재검토</span>
              <span>최근 확인</span>
            </div>
            {shown.map((d) => (
              <button
                key={d.id}
                onClick={() => onSelect(d.id)}
                className={cn(
                  'grid h-9 w-full items-center gap-2 border-b px-4 text-left text-[13px] outline-none hover:bg-accent/60 focus-visible:bg-accent/60',
                  COLS,
                  d.id === selectedId && 'bg-accent'
                )}
              >
                <span className="font-mono text-xs text-muted-foreground">{d.id}</span>
                <span className="truncate">{d.title}</span>
                <span className="truncate font-mono text-xs text-muted-foreground">{d.technology}</span>
                <span>
                  <Badge variant="outline">{statusLabel(d.status)}</Badge>
                </span>
                <Revisit d={d} />
                <span className="text-xs text-muted-foreground">{dateOnly(d.last_checked)}</span>
              </button>
            ))}
            {shown.length === 0 && (
              <div className="p-6 text-[13px] text-muted-foreground">맞는 결정이 없다</div>
            )}
          </div>
        )}
      </div>
      {selectedId && <Panel key={selectedId} id={selectedId} onClose={() => onSelect(null)} />}
    </div>
  )
}

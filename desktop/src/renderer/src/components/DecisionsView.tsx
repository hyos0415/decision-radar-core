import { useEffect, useMemo, useState } from 'react'
import { FileText, Search, X } from 'lucide-react'
import { api, type Decision, type DecisionDetail } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { dateOnly, resultLabel, revisitParts, statusLabel } from '@renderer/lib/format'
import { adrStatusKey, resultKey } from '@renderer/lib/status'
import { PageHeader, Status } from './Status'

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
    <span className="cell-ellipsis text-xs text-muted-foreground">
      {parts.map((p, i) => (
        <span key={i} className={cn(p.strong && 'font-semibold text-foreground')}>
          {i > 0 && ' · '}
          {p.text}
        </span>
      ))}
    </span>
  )
}

function Panel({ id, onClose }: { id: string; onClose: () => void }): React.JSX.Element {
  const [d, setD] = useState<DecisionDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
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
    <aside className="overlay-panel" aria-label="결정 상세">
      <div className="panel-head">
        <span className="eyebrow mono">ADR {id}</span>
        <span className="detail-close-wrap">
          <span className="esc-hint">Esc</span>
          <button className="icon-button" onClick={onClose} aria-label="닫기">
            <X size={16} />
          </button>
        </span>
      </div>
      <div className="panel-scroll">
        {err && <div className="panel-body text-[13px] text-muted-foreground">{err}</div>}
        {!d && !err && (
          <div className="panel-body text-[13px] text-muted-foreground">불러오는 중…</div>
        )}
        {d && (
          <div className="panel-body">
            <h2 className="panel-title">{d.title}</h2>
            <div className="detail-meta">
              <Status status={adrStatusKey(d.status)} label={statusLabel(d.status ?? '')} />
              <span className="mono">{d.technology}</span>
              <span>{d.use}</span>
              <span>{dateOnly(d.date)}</span>
              {d.affected.length > 0 && (
                <span className="mono">
                  {d.affected.map((a) => `${a.repo}/${a.component}`).join(', ')}
                </span>
              )}
            </div>

            <section className="panel-section">
              <h3>재검토 조건</h3>
              {d.revisit.length === 0 ? (
                <p className="text-xs text-muted-foreground">조건이 없다</p>
              ) : (
                d.revisit.map((r) => (
                  <div key={r.id} className="condition">
                    <div>
                      <span className="tag mono">{r.check_type}</span>
                      <Status
                        status={resultKey(r.last?.result)}
                        label={resultLabel(r.last?.result)}
                        strong={r.last?.result === 'met'}
                      />
                    </div>
                    <strong>{r.condition}</strong>
                    {r.last && (
                      <p>
                        {dateOnly(r.last.checked_at)}
                        {r.last.reason && ` · ${r.last.reason}`}
                      </p>
                    )}
                  </div>
                ))
              )}
            </section>

            <div className="file-note">
              <FileText size={15} />
              <span className="mono">{d.file}</span>
              <span>상태는 ADR 파일에서 사람이 바꾼다</span>
            </div>
            <div className="document compact">{d.markdown}</div>
          </div>
        )}
      </div>
    </aside>
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

  useEffect(() => {
    if (!selectedId) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && !e.defaultPrevented) onSelect(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedId, onSelect])

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (items ?? []).filter(
      (d) =>
        (status === 'all' ||
          d.status === status ||
          (status === 'adopted' && d.status === 'accepted')) &&
        (!needle || `${d.id} ${d.title} ${d.technology}`.toLowerCase().includes(needle))
    )
  }, [items, q, status])

  return (
    <div className={cn('page decisions-page', selectedId && 'panel-open')}>
      <PageHeader title="Decisions" count={items?.length}>
        <div className="page-tools">
          <div className="segmented">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                className={cn(status === f.key && 'active')}
                onClick={() => setStatus(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <label className="search">
            <Search size={15} />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="번호·제목·기술 검색"
            />
          </label>
        </div>
      </PageHeader>
      {err && !items ? (
        <div className="empty-state">{err}</div>
      ) : !items ? (
        <div className="empty-state">불러오는 중…</div>
      ) : (
        <div className="decision-table-scroll">
          <div role="table" aria-label="Decisions">
            <div className="decision-grid decision-table-header" role="row">
              <span role="columnheader">번호</span>
              <span role="columnheader">상태</span>
              <span role="columnheader">제목</span>
              <span role="columnheader">기술</span>
              <span role="columnheader">재검토</span>
              <span role="columnheader">최근 확인</span>
            </div>
            <div role="rowgroup">
              {shown.map((d) => (
                <button
                  key={d.id}
                  role="row"
                  onClick={() => onSelect(d.id === selectedId ? null : d.id)}
                  className={cn('decision-grid decision-row', d.id === selectedId && 'selected')}
                >
                  <span className="mono text-muted-foreground" role="cell">
                    {d.id}
                  </span>
                  <span role="cell">
                    <Status status={adrStatusKey(d.status)} label={statusLabel(d.status ?? '')} />
                  </span>
                  <span className="cell-ellipsis decision-title" role="cell" title={d.title}>
                    {d.title}
                  </span>
                  <span
                    className="cell-ellipsis mono muted"
                    role="cell"
                    title={d.technology ?? undefined}
                  >
                    {d.technology}
                  </span>
                  <span className="cell-ellipsis" role="cell">
                    <Revisit d={d} />
                  </span>
                  <span className="mono muted" role="cell">
                    {dateOnly(d.last_checked)}
                  </span>
                </button>
              ))}
            </div>
            {shown.length === 0 && <div className="empty-state">맞는 결정이 없다</div>}
          </div>
        </div>
      )}
      {selectedId && <Panel key={selectedId} id={selectedId} onClose={() => onSelect(null)} />}
    </div>
  )
}

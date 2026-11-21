import { useCallback, useEffect, useMemo, useState } from 'react'
import { FileText, FlaskConical, Search, X } from 'lucide-react'
import { api, type Decision, type DecisionDetail, type ExperimentResult } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { dateOnly, resultLabel, revisitParts, statusLabel } from '@renderer/lib/format'
import { adrStatusKey, resultKey, type StatusKey } from '@renderer/lib/status'
import { Markdown } from './Markdown'
import { PageHeader, Status } from './Status'
import { Button } from './ui/button'
import { COPY } from '../../../copy'

const E = COPY.decisions.experiment
const EXPERIMENT_POLL_MS = 3000
const BROKEN = ['fail', 'install_failed', 'baseline_failed', 'baseline_mismatch', 'error']

function verdictKey(r: ExperimentResult): StatusKey {
  if (r.ok) return 'done'
  return BROKEN.includes(r.verdict) ? 'failed' : 'undetermined'
}

function ExperimentRun({ r, open }: { r: ExperimentResult; open: boolean }): React.JSX.Element {
  return (
    <div className="condition">
      <div>
        <Status
          status={verdictKey(r)}
          label={r.verdict_text}
          strong={!r.ok && BROKEN.includes(r.verdict)}
        />
        {r.stub && <span className="tag mono">{E.stub}</span>}
      </div>
      <p>
        {dateOnly(r.at)}
        {r.seconds != null && r.seconds > 0 && ` · ${Math.round(r.seconds)}s`}
        {r.export?.pr_url && (
          <>
            {' · '}
            <a href={r.export.pr_url} target="_blank" rel="noreferrer" className="hover:underline">
              {E.pr}
            </a>
          </>
        )}
        {r.export?.skipped_reason && ` · ${r.export.skipped_reason}`}
      </p>
      {open && r.report_md && <Markdown compact>{r.report_md}</Markdown>}
    </div>
  )
}

// 실험해 보기 (HYO-116) — 승인한 결정을 레시피로. 접수하면 작업 하나 → 끝날 때까지 이 패널이 지켜보고 상세를 다시 읽는다.
// 돌릴 수 없는 결정은 이유만 보인다. 내보내기(draft PR)는 실사용 모드에서만 켤 수 있다
function Experiment({
  d,
  onDone
}: {
  d: DecisionDetail
  onDone: () => void
}): React.JSX.Element | null {
  const plan = d.experiment
  const [jobId, setJobId] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [exportPr, setExportPr] = useState(false)

  useEffect(() => {
    if (!jobId) return
    const timer = setInterval(() => {
      api
        .job(jobId)
        .then((j) => {
          if (j.state === 'queued' || j.state === 'running') return
          setJobId(null)
          if (j.state === 'error') setErr(E.failed(j.error ?? ''))
          onDone()
        })
        .catch(() => {}) // backend did not answer — try again next tick
    }, EXPERIMENT_POLL_MS)
    return () => clearInterval(timer)
  }, [jobId, onDone])

  if (!plan) return null
  const start = (): void => {
    setErr(null)
    api
      .submitExperiment(d.id, exportPr && plan.export_allowed)
      .then((j) => setJobId(j.id))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }
  const [latest, ...past] = d.experiments ?? []

  return (
    <section className="panel-section">
      <h3>{E.title}</h3>
      {!plan.recipe ? (
        <p className="text-xs text-muted-foreground">{plan.reason ?? E.none}</p>
      ) : (
        <div className="condition">
          <div>
            <span className="inline-flex items-center gap-1.5">
              <FlaskConical size={14} className="text-muted-foreground" />
              <strong>{plan.label}</strong>
            </span>
            {plan.cost_text && <span className="tag">{plan.cost_text}</span>}
          </div>
          {plan.detail && <p>{plan.detail}</p>}
          {!plan.ready && plan.reason && <p className="tone-warning">{plan.reason}</p>}
          {plan.ready && (
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <Button size="sm" disabled={jobId !== null} onClick={start}>
                {jobId ? E.running : E.run}
              </Button>
              {plan.exports ? (
                <label
                  className={cn(
                    'flex items-center gap-1.5 text-xs',
                    !plan.export_allowed && 'text-muted-foreground'
                  )}
                  title={plan.export_allowed ? (plan.export_rule ?? undefined) : E.exportOff}
                >
                  <input
                    type="checkbox"
                    checked={exportPr && plan.export_allowed}
                    disabled={!plan.export_allowed || jobId !== null}
                    onChange={(e) => setExportPr(e.target.checked)}
                  />
                  {E.exportLabel}
                </label>
              ) : (
                plan.export_rule && (
                  <span className="text-xs text-muted-foreground">{plan.export_rule}</span>
                )
              )}
            </div>
          )}
          {jobId && <p className="text-xs text-muted-foreground">{E.runningHint}</p>}
          {err && <p className="tone-warning text-xs">{err}</p>}
        </div>
      )}
      {latest && <ExperimentRun r={latest} open />}
      {past.length > 0 && (
        <>
          <p className="mt-2 text-xs text-muted-foreground">{E.past}</p>
          {past.map((r) => (
            <ExperimentRun key={r.at + r.verdict} r={r} open={false} />
          ))}
        </>
      )}
      {(plan.recipe || latest) && <p className="text-xs text-muted-foreground">{E.note}</p>}
    </section>
  )
}

const FILTERS: { key: string; label: string }[] = Object.entries(COPY.decisions.filters).map(
  ([key, label]) => ({ key, label })
)

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

  // an experiment ended — reread the detail so its result shows (the ADR itself is unchanged)
  const reload = useCallback((): void => {
    api
      .decision(id)
      .then(setD)
      .catch(() => {})
  }, [id])

  return (
    <aside className="overlay-panel" aria-label={COPY.decisions.detailLabel}>
      <div className="panel-head">
        <span className="eyebrow mono">ADR {id}</span>
        <span className="detail-close-wrap">
          <span className="esc-hint">Esc</span>
          <button className="icon-button" onClick={onClose} aria-label={COPY.term.close}>
            <X size={16} />
          </button>
        </span>
      </div>
      <div className="panel-scroll">
        {err && <div className="panel-body text-[13px] text-muted-foreground">{err}</div>}
        {!d && !err && (
          <div className="panel-body text-[13px] text-muted-foreground">{COPY.term.loading}</div>
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
              <h3>{COPY.decisions.revisitConditions}</h3>
              {d.revisit.length === 0 ? (
                <p className="text-xs text-muted-foreground">{COPY.decisions.noConditions}</p>
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

            <Experiment d={d} onDone={reload} />

            <div className="file-note">
              <FileText size={15} />
              <span className="mono">{d.file}</span>
              <span>{COPY.decisions.statusNote}</span>
            </div>
            <Markdown compact>{d.markdown}</Markdown>
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
      <PageHeader title={COPY.term.decisions} count={items?.length}>
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
              placeholder={COPY.decisions.search}
            />
          </label>
        </div>
      </PageHeader>
      {err && !items ? (
        <div className="empty-state">{err}</div>
      ) : !items ? (
        <div className="empty-state">{COPY.term.loading}</div>
      ) : (
        <div className="decision-table-scroll">
          <div role="table" aria-label={COPY.term.decisions}>
            <div className="decision-grid decision-table-header" role="row">
              <span role="columnheader">{COPY.decisions.columns.id}</span>
              <span role="columnheader">{COPY.decisions.columns.status}</span>
              <span role="columnheader">{COPY.decisions.columns.title}</span>
              <span role="columnheader">{COPY.decisions.columns.technology}</span>
              <span role="columnheader">{COPY.decisions.columns.revisit}</span>
              <span role="columnheader">{COPY.decisions.columns.lastChecked}</span>
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
            {shown.length === 0 && <div className="empty-state">{COPY.decisions.empty}</div>}
          </div>
        </div>
      )}
      {selectedId && <Panel key={selectedId} id={selectedId} onClose={() => onSelect(null)} />}
    </div>
  )
}

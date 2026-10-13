import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { Job, RunResult, Schedule } from '@renderer/lib/api'
import { dayLabel, resultLabel, timeOnly } from '@renderer/lib/format'
import { cn } from '@renderer/lib/utils'
import type { StatusKey } from '@renderer/lib/status'
import { PageHeader, StatusIcon } from './Status'

const TRIGGER_LABEL: Record<string, string> = {
  manual: '수동',
  job: '수동',
  schedule: '정기'
}

function runSummary(r: RunResult): string {
  if (!r.ok) return r.error ?? '실패'
  return r.summary.join(' · ') || '—'
}

function RunRow({
  status,
  time,
  flow,
  trigger,
  summary,
  open,
  onToggle
}: {
  status: StatusKey
  time: string
  flow: string
  trigger: string
  summary: string
  open?: boolean
  onToggle?: () => void
}): React.JSX.Element {
  const content = (
    <>
      <StatusIcon status={status} />
      <span className="mono run-history-time">{time}</span>
      <span className="mono run-history-flow">{flow}</span>
      <span className="tag">{trigger}</span>
      <span className="run-history-summary" title={summary}>
        {summary}
      </span>
      {onToggle ? <ChevronRight className="feed-chevron" size={14} /> : <span />}
    </>
  )
  return onToggle ? (
    <button
      className={cn('run-history-row', open && 'open')}
      onClick={onToggle}
      aria-expanded={open}
    >
      {content}
    </button>
  ) : (
    <div className="run-history-row" style={{ cursor: 'default' }}>
      {content}
    </div>
  )
}

function RunDetail({
  r,
  onOpenDecision
}: {
  r: RunResult
  onOpenDecision: (id: string) => void
}): React.JSX.Element {
  return (
    <div className="run-detail">
      {r.error && <div>{r.error}</div>}
      {r.summary.length > 0 && (
        <ul>
          {r.summary.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      )}
      {r.drafts.length > 0 && (
        <section>
          <h3>초안</h3>
          <ul>
            {r.drafts.map((d) => (
              <li key={d.adr}>
                <button className="link-button" onClick={() => onOpenDecision(d.adr)}>
                  <span className="mono text-xs text-muted-foreground">{d.adr}</span> {d.title}
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {r.alerts.length > 0 && (
        <section>
          <h3>재검토 알림</h3>
          <ul>
            {r.alerts.map((a, i) => (
              <li key={i}>
                <button
                  className="link-button mono text-xs text-muted-foreground"
                  onClick={() => onOpenDecision(a.adr)}
                >
                  {a.adr}
                </button>
                <span className="mono text-xs text-muted-foreground">{a.condition}</span>
                <span className="font-semibold">{resultLabel(a.result)}</span>
                {a.message && <span className="truncate text-muted-foreground">{a.message}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {r.mode === 'stub' && <span className="tag mono w-fit">스텁</span>}
    </div>
  )
}

export function RunsView({
  runs,
  error,
  schedule,
  active,
  onOpenDecision
}: {
  runs: RunResult[] | null // newest first
  error: string | null
  schedule: Schedule | null
  active: Job[] // detect · revisit jobs queued or running
  onOpenDecision: (id: string) => void
}): React.JSX.Element {
  const [openId, setOpenId] = useState<string | null>(null)

  const groups: { label: string; items: RunResult[] }[] = []
  for (const r of runs ?? []) {
    const label = dayLabel(r.at)
    const last = groups[groups.length - 1]
    if (last && last.label === label) last.items.push(r)
    else groups.push({ label, items: [r] })
  }

  return (
    <div className="page">
      <PageHeader title="실행 기록" />
      <div className="feed">
        {schedule?.enabled && schedule.flows.length > 0 && (
          <section className="feed-group">
            <h2>예정</h2>
            {schedule.flows.map((f) => (
              <RunRow
                key={f.flow}
                status="not_met"
                time={timeOnly(f.next_slot, true)}
                flow={f.flow}
                trigger="정기"
                summary=""
              />
            ))}
          </section>
        )}
        {active.length > 0 && (
          <section className="feed-group">
            <h2>실행 중</h2>
            {active.map((j) => (
              <RunRow
                key={j.id}
                status="running"
                time={timeOnly(j.started ?? j.created)}
                flow={j.kind}
                trigger="수동"
                summary={j.message ?? ''}
              />
            ))}
          </section>
        )}
        {error && <div className="empty-state">{error}</div>}
        {!runs && !error && <div className="empty-state">불러오는 중…</div>}
        {runs && runs.length === 0 && <div className="empty-state">아직 실행한 적이 없다</div>}
        {groups.map((g) => (
          <section key={g.label} className="feed-group">
            <h2>{g.label}</h2>
            {g.items.map((r) => (
              <div key={r.id}>
                <RunRow
                  status={r.ok ? 'done' : 'failed'}
                  time={timeOnly(r.at)}
                  flow={r.flow}
                  trigger={TRIGGER_LABEL[r.trigger] ?? r.trigger}
                  summary={runSummary(r)}
                  open={openId === r.id}
                  onToggle={() => setOpenId(openId === r.id ? null : r.id)}
                />
                {openId === r.id && <RunDetail r={r} onOpenDecision={onOpenDecision} />}
              </div>
            ))}
          </section>
        ))}
      </div>
    </div>
  )
}

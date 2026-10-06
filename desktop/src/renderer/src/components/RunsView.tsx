import { useCallback, useEffect, useState } from 'react'
import { api, type Flow, type RunResult } from '@renderer/lib/api'
import { dateTime, resultLabel } from '@renderer/lib/format'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

function RunRow({
  r,
  onOpenDecision
}: {
  r: RunResult
  onOpenDecision: (id: string) => void
}): React.JSX.Element {
  return (
    <li className="border-b px-6 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span className="font-mono">{dateTime(r.at)}</span>
        <Badge variant="outline">{r.flow}</Badge>
        <Badge variant={r.ok ? 'secondary' : 'default'}>{r.ok ? 'ok' : '실패'}</Badge>
        {r.mode === 'stub' && <span>스텁</span>}
      </div>
      {r.error && <div className="mt-1.5 text-xs">{r.error}</div>}
      {r.summary.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[13px]">
          {r.summary.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      )}
      {r.drafts.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-[11px] text-muted-foreground">초안</div>
          <ul className="space-y-0.5">
            {r.drafts.map((d) => (
              <li key={d.adr}>
                <button
                  onClick={() => onOpenDecision(d.adr)}
                  className="flex items-baseline gap-2 text-left text-[13px] hover:underline"
                >
                  <span className="font-mono text-xs text-muted-foreground">{d.adr}</span>
                  <span>{d.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {r.alerts.length > 0 && (
        <div className="mt-2">
          <div className="mb-1 text-[11px] text-muted-foreground">재검토 알림</div>
          <ul className="space-y-0.5">
            {r.alerts.map((a, i) => (
              <li key={i} className="flex items-baseline gap-2 text-[13px]">
                <button
                  onClick={() => onOpenDecision(a.adr)}
                  className="font-mono text-xs text-muted-foreground hover:underline"
                >
                  {a.adr}
                </button>
                <span className="font-mono text-xs text-muted-foreground">{a.condition}</span>
                <span className="font-semibold">{resultLabel(a.result)}</span>
                {a.message && <span className="truncate text-muted-foreground">{a.message}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  )
}

export function RunsView({
  refreshKey,
  onOpenDecision,
  onChanged
}: {
  refreshKey: number
  onOpenDecision: (id: string) => void
  onChanged: () => void
}): React.JSX.Element {
  const [runs, setRuns] = useState<RunResult[] | null>(null)
  const [running, setRunning] = useState<Flow | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .runs()
      .then((r) => setRuns(r))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)))
  }, [])

  useEffect(load, [load, refreshKey])

  async function start(flow: Flow): Promise<void> {
    setRunning(flow)
    setErr(null)
    try {
      await api.run(flow)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(null)
      load()
      onChanged()
    }
  }

  const caption = '스텁 모델 — 과금 없음'
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex h-11 shrink-0 items-center border-b px-4 text-[13px] font-semibold">
        실행
      </div>
      <div className="flex-1 overflow-y-auto">
        <div className="flex gap-8 border-b px-6 py-4">
          {(['detect', 'revisit'] as const).map((f) => (
            <div key={f} className="flex flex-col items-start gap-1.5">
              <Button variant="outline" disabled={running !== null} onClick={() => void start(f)}>
                {running === f ? '실행 중…' : `${f} 실행`}
              </Button>
              <span className="text-[11px] text-muted-foreground">{caption}</span>
            </div>
          ))}
        </div>
        {err && (
          <div className="border-b bg-muted px-6 py-2 text-xs" role="status">
            {err}
          </div>
        )}
        <div className="px-6 pt-3 pb-1 text-[11px] font-medium text-muted-foreground">기록</div>
        {!runs ? (
          <div className="px-6 py-3 text-[13px] text-muted-foreground">불러오는 중…</div>
        ) : runs.length === 0 ? (
          <div className="px-6 py-3 text-[13px] text-muted-foreground">아직 실행한 적이 없다</div>
        ) : (
          <ul className="border-t">
            {runs.map((r) => (
              <RunRow key={r.id} r={r} onOpenDecision={onOpenDecision} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

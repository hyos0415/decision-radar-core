import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { api, type WeekList, type WeekListItem } from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { PageHeader } from './Status'
import { COPY } from '../../../copy'

const T = COPY.weekList

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** folded_into를 따라 대표 아래로 묶는다 — 대표가 목록에 없으면 그 항목이 대표다. 순서는 파일 그대로 */
function fold(items: WeekListItem[]): { head: WeekListItem; rest: WeekListItem[] }[] {
  const byId = new Map(items.map((x) => [x.id, x]))
  const root = (x: WeekListItem): string => {
    const seen = new Set<string>()
    let cur = x
    while (cur.folded_into && byId.has(cur.folded_into) && !seen.has(cur.id)) {
      seen.add(cur.id)
      cur = byId.get(cur.folded_into)!
    }
    return cur.id
  }
  const groups = new Map<string, { head: WeekListItem; rest: WeekListItem[] }>()
  for (const x of items) {
    const r = root(x)
    if (r === x.id) groups.set(r, { head: x, rest: groups.get(r)?.rest ?? [] })
    else {
      const g = groups.get(r)
      if (g) g.rest.push(x)
      else groups.set(r, { head: byId.get(r)!, rest: [x] })
    }
  }
  return [...groups.values()]
}

// 링크는 새 창 요청으로 낸다 — main이 가로채 기본 브라우저로 연다 (setWindowOpenHandler)
function Title({ item }: { item: WeekListItem }): React.JSX.Element {
  const text = item.title ?? item.id
  if (!item.url) return <span className="min-w-0 truncate text-[13px]">{text}</span>
  return (
    <a
      href={item.url}
      target="_blank"
      rel="noreferrer"
      title={`${text} — ${T.open}`}
      className="min-w-0 truncate text-[13px] hover:underline"
    >
      {text}
    </a>
  )
}

// 이 주 detect가 줄마다 판정을 남기기 전 실행이면 true — 「판정 없음」 설명이 달라진다
const OldRun = createContext(false)

/** detect 판정 배지 — 판정이 없으면(아직 안 돌았으면) 「초안 후보」 */
function Verdict({ item }: { item: WeekListItem }): React.JSX.Element | null {
  const old = useContext(OldRun)
  if (!item.to_detect) return null
  const d = item.detect
  if (!d) return <span className="tag tag-strong">{T.badge.toDetect}</span>
  const V = T.verdict
  const [label, cls, title] = {
    draft: [V.draft(d.adr ?? null), 'tag-strong', undefined],
    existing: [V.existing(d.adr ?? null), '', T.verdictTitle.existing],
    excluded: [d.reason?.startsWith('이미 반영됨') ? V.applied : V.excluded, '', d.reason],
    dropped: [V.dropped, '', d.reason],
    failed: [V.failed, 'tag-warning', d.reason],
    unjudged: [V.unjudged, '', old ? T.verdictTitle.old : T.verdictTitle.unjudged]
  }[d.verdict] as [string, string, string | null | undefined]
  return (
    <span className={cn('tag', cls)} title={title ?? undefined}>
      {label}
    </span>
  )
}

function Badges({ item }: { item: WeekListItem }): React.JSX.Element {
  return (
    <span className="flex shrink-0 items-center gap-1">
      {item.change === 'security' && <span className="tag tag-danger">{T.badge.security}</span>}
      {item.alarm && item.alarm.length > 0 && (
        <span className="tag tag-warning" title={T.alarmTitle(item.alarm)}>
          {T.badge.alarm}
        </span>
      )}
      <Verdict item={item} />
      {item.tier && <span className="tag mono">{item.tier}</span>}
      {item.fit != null && <span className="tag">{T.fit(item.fit)}</span>}
      {item.source && <span className="tag mono">{item.source}</span>}
      {item.points != null && <span className="tag mono">{T.points(item.points)}</span>}
    </span>
  )
}

/** 한 줄 — 순위 · 제목 링크 · 짧은 이유 · 배지. security는 왼쪽 띠로 강조 */
function Row({
  item,
  nested,
  children
}: {
  item: WeekListItem
  nested?: boolean
  children?: React.ReactNode
}): React.JSX.Element {
  return (
    <li
      className={cn(
        'flex min-w-0 items-center gap-2 border-b py-2 pr-(--gutter)',
        nested ? 'pl-[calc(var(--gutter)+28px)]' : 'pl-(--gutter)',
        item.change === 'security' && 'week-row-security'
      )}
    >
      <span className="mono w-5 shrink-0 text-right text-xs text-muted-foreground">
        {item.rank ?? ''}
      </span>
      <Title item={item} />
      {item.reason && (
        <span
          className="min-w-0 max-w-[40%] shrink truncate text-xs text-muted-foreground"
          title={item.reason}
        >
          {item.reason}
        </span>
      )}
      {children}
      <span className="ml-auto" />
      <Badges item={item} />
    </li>
  )
}

/** 대표 한 줄 + 「외 N건」 — 펼치면 접힌 항목이 들여쓰기로 보인다 */
function FoldedRows({ items }: { items: WeekListItem[] }): React.JSX.Element {
  return (
    <ul className="m-0 list-none p-0">
      {fold(items).map(({ head, rest }) => (
        <FoldedGroup key={head.id} head={head} rest={rest} />
      ))}
    </ul>
  )
}

function FoldedGroup({
  head,
  rest
}: {
  head: WeekListItem
  rest: WeekListItem[]
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Row item={head}>
        {rest.length > 0 && (
          <button
            className="link-button shrink-0 text-xs text-muted-foreground"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? T.foldedHide : T.folded(rest.length)}
          </button>
        )}
      </Row>
      {open && rest.map((x) => <Row key={x.id} item={x} nested />)}
    </>
  )
}

function SubHead({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="flex min-w-0 items-baseline gap-2 border-b bg-muted/40 px-(--gutter) py-1.5 text-xs">
      {children}
    </div>
  )
}

function Section({
  title,
  count,
  children
}: {
  title: string
  count: number
  children: React.ReactNode
}): React.JSX.Element {
  return (
    <section className="feed-group">
      <h2>
        {title}
        <span className="mono ml-2">{count}</span>
      </h2>
      {children}
    </section>
  )
}

export function WeekListView({ refreshKey }: { refreshKey: number }): React.JSX.Element {
  const [data, setData] = useState<WeekList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notOursOpen, setNotOursOpen] = useState(false)
  // 사용자가 고른 주차 — null이면 서버가 주는 가장 새 주차
  const week = useRef<string | null>(null)
  // 주차를 빨리 바꾸면 늦게 온 앞 요청이 화면을 덮지 않게 — 마지막 요청만 반영한다
  const seq = useRef(0)

  const load = useCallback((w?: string | null): Promise<void> => {
    if (w !== undefined) week.current = w
    const n = ++seq.current
    return api
      .weekList(week.current ?? undefined)
      .then((d) => {
        if (n !== seq.current) return
        setData(d)
        setError(null)
      })
      .catch((e) => {
        if (n === seq.current) setError(errText(e))
      })
  }, [])

  useEffect(() => {
    void load()
  }, [load, refreshKey])

  const current = data?.week ?? null
  const techCount = data?.tech.reduce((n, t) => n + t.items.length, 0) ?? 0
  const slotCount = data?.slots.reduce((n, s) => n + s.items.length, 0) ?? 0
  const total = data
    ? data.always.length + data.alarm.length + techCount + slotCount + data.not_ours.length
    : 0
  const toDetect = data?.counts.to_detect
  const health = data?.source_health ?? null
  const run = data?.detect_run ?? null
  const runNote = run ? T.detectRun(run.at.slice(5, 16).replace('T', ' ')) : T.detectNotYet

  return (
    <div className="page">
      <PageHeader
        title={COPY.term.weekList}
        subtitle={toDetect !== undefined ? `${T.toDetect(toDetect)} · ${runNote}` : undefined}
      >
        {data && data.weeks.length > 0 && (
          <label className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
            {T.week}
            <select
              className="h-7 rounded-md border bg-background px-1.5 text-xs text-foreground"
              value={current ?? ''}
              onChange={(e) => void load(e.target.value)}
            >
              {data.weeks.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </label>
        )}
      </PageHeader>

      {health && health.problems.length > 0 && (
        <div
          className="tone-warning cursor-default border-b px-(--gutter) py-1.5 text-xs"
          title={T.healthTitle(health.problems)}
        >
          {health.line}
        </div>
      )}

      {error && !data && <div className="empty-state">{error}</div>}
      {error && data && <div className="px-(--gutter) py-2 text-xs">{error}</div>}
      {!data && !error && <div className="empty-state">{COPY.term.loading}</div>}
      {data && current === null && <div className="empty-state">{T.empty}</div>}
      {data && current !== null && total === 0 && <div className="empty-state">{T.emptyWeek}</div>}

      {data && current !== null && total > 0 && (
        <OldRun.Provider value={run !== null && !run.recorded}>
          <div className="feed">
            <Section title={T.sections.always} count={data.always.length}>
              <FoldedRows items={data.always} />
            </Section>

            {data.alarm.length > 0 && (
              <Section title={T.sections.alarm} count={data.alarm.length}>
                <FoldedRows items={data.alarm} />
              </Section>
            )}

            <Section title={T.sections.tech} count={techCount}>
              {data.tech.map((t) => (
                <div key={t.query}>
                  <SubHead>
                    <span className="mono font-medium text-foreground">{t.query}</span>
                  </SubHead>
                  <FoldedRows items={t.items} />
                </div>
              ))}
            </Section>

            <Section title={T.sections.slots} count={slotCount}>
              {data.slots.map((s) => (
                <div key={s.slot}>
                  <SubHead>
                    <span className="font-medium text-foreground">{s.name ?? s.slot}</span>
                    {s.name && <span className="mono text-muted-foreground">{s.slot}</span>}
                    <span className="min-w-0 truncate text-muted-foreground">
                      {T.current(s.current ?? [])}
                    </span>
                  </SubHead>
                  {s.items.length > 0 ? (
                    <FoldedRows items={s.items} />
                  ) : (
                    <div className="border-b px-(--gutter) py-2 pl-[calc(var(--gutter)+28px)] text-xs text-muted-foreground">
                      {T.none}
                    </div>
                  )}
                </div>
              ))}
            </Section>

            {data.not_ours.length > 0 && (
              <section className="feed-group">
                <h2>
                  <button
                    className="flex items-center gap-1.5"
                    aria-expanded={notOursOpen}
                    onClick={() => setNotOursOpen((o) => !o)}
                  >
                    <ChevronRight
                      size={13}
                      className={cn('transition-transform', notOursOpen && 'rotate-90')}
                    />
                    {T.notOursToggle(data.not_ours.length)}
                  </button>
                </h2>
                {notOursOpen && <FoldedRows items={data.not_ours} />}
              </section>
            )}
          </div>
        </OldRun.Provider>
      )}
    </div>
  )
}

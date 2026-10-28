import { useCallback, useEffect, useRef, useState } from 'react'
import { Bookmark, BookmarkCheck, ChevronRight, ExternalLink, Zap } from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
  type SavedTopic,
  type TopicList,
  type TopicParentCandidate,
  type TopicParentVerdict,
  type TopicPost,
  type TopicRow
} from '@renderer/lib/api'
import { cn } from '@renderer/lib/utils'
import { dateOnly } from '@renderer/lib/format'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { PageHeader } from './Status'
import { COPY } from '../../../copy'

const T = COPY.topics

type Tab = 'week' | 'saved'

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/** 출처 이름과 글 수 — `hn 3` */
function SourceBadges({ sources }: { sources: Record<string, number> }): React.JSX.Element {
  return (
    <span className="flex flex-wrap gap-1">
      {Object.entries(sources).map(([name, n]) => (
        <span key={name} className="tag mono">
          {name} {n}
        </span>
      ))}
    </span>
  )
}

// 링크는 새 창 요청으로 낸다 — main이 가로채 기본 브라우저로 연다 (setWindowOpenHandler)
function ExternalLinkText({
  href,
  children,
  className
}: {
  href: string
  children: React.ReactNode
  className?: string
}): React.JSX.Element {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      title={T.open}
      className={cn('hover:underline', className)}
    >
      {children}
    </a>
  )
}

function PostList({ posts }: { posts: TopicPost[] }): React.JSX.Element {
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {posts.map((p, i) => (
        <li key={`${p.url}-${i}`} className="flex min-w-0 items-baseline gap-2 text-xs">
          <span className="tag mono">{p.source}</span>
          <ExternalLinkText href={p.url} className="min-w-0 truncate">
            {p.title}
          </ExternalLinkText>
          {p.signal && <span className="shrink-0 text-muted-foreground">{p.signal}</span>}
        </li>
      ))}
    </ul>
  )
}

// 직접 묶기 — 「이 화제는 ___의 하위」. 제목으로 같은 주 화제를 찾아 하나를 고른다
function AttachParent({
  topic,
  onPick,
  onCancel
}: {
  topic: TopicRow
  onPick: (parentId: string) => void
  onCancel: () => void
}): React.JSX.Element {
  const [q, setQ] = useState('')
  const [items, setItems] = useState<TopicParentCandidate[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const timer = setTimeout(
      () => {
        api
          .topicParentCandidates(topic.week, q.trim())
          .then((list) => {
            if (cancelled) return
            setItems(list.filter((c) => c.id !== topic.id))
            setError(null)
          })
          .catch((e) => {
            if (!cancelled) setError(errText(e))
          })
      },
      q ? 200 : 0
    )
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [topic.week, topic.id, q])

  return (
    <div className="flex flex-col gap-2 rounded-md border bg-muted/40 p-3">
      <div className="flex items-center gap-2 text-xs">
        <span className="shrink-0">{T.parent.attachPrompt}</span>
        <Input
          autoFocus
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.stopPropagation()
              onCancel()
            }
          }}
          placeholder={T.parent.attachPlaceholder}
          aria-label={T.parent.attachPlaceholder}
        />
        <span className="shrink-0">{T.parent.attachSuffix}</span>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          {T.parent.cancel}
        </Button>
      </div>
      {error ? (
        <div className="text-xs">{error}</div>
      ) : !items ? (
        <div className="text-xs text-muted-foreground">{T.parent.searching}</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-muted-foreground">{T.parent.noCandidates}</div>
      ) : (
        <ul className="m-0 flex max-h-48 list-none flex-col overflow-auto p-0">
          {items.map((c) => (
            <li key={c.id}>
              <button
                className="flex w-full items-baseline gap-2 rounded px-2 py-1 text-left text-xs hover:bg-accent"
                onClick={() => onPick(c.id)}
              >
                <span className="mono shrink-0 text-muted-foreground">{T.parent.rank(c.rank)}</span>
                <span className="min-w-0 truncate">{c.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// 제안 배지 + 맞음/아님 — 제안이 있어도 화제를 숨기거나 접거나 옮기지 않는다 (hier-registration v3)
function ParentSuggestion({
  topic,
  busy,
  onAnswer
}: {
  topic: TopicRow
  busy: boolean
  onAnswer: (parentId: string, verdict: TopicParentVerdict) => void
}): React.JSX.Element | null {
  const s = topic.parent_suggestion
  if (!s) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="tag" title={`${T.parent.rule[s.rule]} · ${s.title}`}>
        {T.parent.badge(s.title, s.rank)}
      </span>
      <div className="segmented segmented-sm" role="group" aria-label={T.parent.changeHint}>
        <button
          className={cn(s.verdict === 'accept' && 'active')}
          aria-pressed={s.verdict === 'accept'}
          disabled={busy}
          onClick={() => onAnswer(s.topic_id, 'accept')}
        >
          {T.parent.accept}
        </button>
        <button
          className={cn(s.verdict === 'reject' && 'active')}
          aria-pressed={s.verdict === 'reject'}
          disabled={busy}
          onClick={() => onAnswer(s.topic_id, 'reject')}
        >
          {T.parent.reject}
        </button>
      </div>
      {s.verdict && (
        <span className="text-muted-foreground">
          {T.parent.answered[s.verdict]} · {T.parent.changeHint}
        </span>
      )}
    </div>
  )
}

function TopicItem({
  topic,
  busy,
  onToggleSave,
  onAnswer,
  onAttach,
  onRelease
}: {
  topic: TopicRow
  busy: boolean
  onToggleSave: () => void
  onAnswer: (parentId: string, verdict: TopicParentVerdict) => void
  onAttach: (parentId: string) => void
  onRelease: () => void
}): React.JSX.Element {
  const [open, setOpen] = useState(false)
  const [attaching, setAttaching] = useState(false)
  const manual = topic.parent_manual

  return (
    <li className="border-b px-(--gutter) py-3">
      <div className="flex items-start gap-3">
        <span className="mono w-7 shrink-0 pt-0.5 text-right text-xs text-muted-foreground">
          {topic.rank}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex min-w-0 items-baseline gap-2">
            <ExternalLinkText href={topic.url} className="min-w-0 truncate text-[13px] font-medium">
              {topic.title}
            </ExternalLinkText>
            <ExternalLink size={12} className="shrink-0 text-muted-foreground" aria-hidden />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {topic.signal && <span>{topic.signal}</span>}
            <SourceBadges sources={topic.sources} />
            <span>{T.posts(topic.n_items)}</span>
          </div>
          <ParentSuggestion topic={topic} busy={busy} onAnswer={onAnswer} />
          {manual && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="tag">{T.parent.manualLabel(manual.title)}</span>
              <button className="link-button" disabled={busy} onClick={onRelease}>
                {T.parent.release}
              </button>
            </div>
          )}
          {attaching && (
            <AttachParent
              topic={topic}
              onCancel={() => setAttaching(false)}
              onPick={(id) => {
                setAttaching(false)
                onAttach(id)
              }}
            />
          )}
          {open && <PostList posts={topic.posts} />}
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
            >
              <ChevronRight size={13} className={cn('transition-transform', open && 'rotate-90')} />
              {open ? T.hidePosts : T.showPosts}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={onToggleSave}>
              {topic.saved ? <BookmarkCheck size={13} /> : <Bookmark size={13} />}
              {topic.saved ? T.unsave : T.save}
            </Button>
            <Button
              size="sm"
              variant="outline"
              title={T.captureTitle}
              onClick={() => void window.radar.openCapture(topic.capture_seed)}
            >
              <Zap size={13} />
              {T.capture}
            </Button>
            {!manual && !attaching && (
              <Button size="sm" variant="ghost" onClick={() => setAttaching(true)}>
                {T.parent.attach}
              </Button>
            )}
          </div>
        </div>
      </div>
    </li>
  )
}

function SavedList({
  items,
  onUnsave
}: {
  items: SavedTopic[]
  onUnsave: (id: string) => void
}): React.JSX.Element {
  return (
    <ul className="m-0 list-none p-0">
      {items.map((t) => (
        <li key={t.id} className="flex items-start gap-3 border-b px-(--gutter) py-3">
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <ExternalLinkText href={t.url} className="min-w-0 truncate text-[13px] font-medium">
              {t.title}
            </ExternalLinkText>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span className="mono">{t.week}</span>
              <SourceBadges sources={t.sources} />
              <span>{T.posts(t.n_items)}</span>
              <span>{T.savedAt(dateOnly(t.saved_at))}</span>
            </div>
            <PostList posts={t.posts} />
          </div>
          <Button size="sm" variant="outline" onClick={() => onUnsave(t.id)}>
            <BookmarkCheck size={13} />
            {T.unsave}
          </Button>
        </li>
      ))}
    </ul>
  )
}

export function TopicsView({ refreshKey }: { refreshKey: number }): React.JSX.Element {
  const [tab, setTab] = useState<Tab>('week')
  const [data, setData] = useState<TopicList | null>(null)
  const [saved, setSaved] = useState<SavedTopic[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  // 사용자가 고른 주차 — null이면 서버가 주는 가장 새 주차
  const week = useRef<string | null>(null)
  // 「더 보기」로 펼친 개수 (HYO-154) — null이면 k. 주차를 바꾸면 다시 k
  const limit = useRef<number | null>(null)

  const load = useCallback((w?: string | null, n?: number | null): Promise<void> => {
    if (w !== undefined) {
      week.current = w
      limit.current = null
    }
    if (n !== undefined) limit.current = n
    return api
      .topics(week.current ?? undefined, limit.current ?? undefined)
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e) => setError(errText(e)))
  }, [])

  const loadSaved = useCallback(
    (): Promise<void> =>
      api
        .savedTopics()
        .then((l) => setSaved(l))
        .catch((e) => setError(errText(e))),
    []
  )

  useEffect(() => {
    void load()
    void loadSaved()
  }, [load, loadSaved, refreshKey])

  // 서버가 돌려준 결과를 보여 주고 목록을 다시 읽는다 — 행은 서버 상태를 그대로 따른다
  async function act(
    id: string,
    fn: () => Promise<{ ok: boolean; message: string }>
  ): Promise<void> {
    setBusyId(id)
    try {
      const r = await fn()
      if (!r.ok) toast(r.message)
    } catch (e) {
      toast(errText(e))
    } finally {
      await Promise.all([load(), loadSaved()])
      setBusyId(null)
    }
  }

  const current = data?.week ?? null
  const rows = data?.items ?? []
  const k = data?.k ?? 0
  const head = rows.slice(0, k)
  // 상위 k 뒤는 파이프라인 파일 순위 그대로 한 묶음 — 출처 자리 나눔은 상위 k에만 있다
  const more = rows.slice(k)
  const groups = [
    ...(['multi', 'single'] as const).map((s) => ({
      key: s as string,
      title: T.sections[s],
      items: head.filter((r) => r.section === s)
    })),
    { key: 'more', title: T.moreSection(k + 1), items: more }
  ].filter((g) => g.items.length > 0)
  const canMore = !!data && rows.length < data.max_limit

  return (
    <div className="page">
      <PageHeader title={COPY.term.topics} count={tab === 'week' ? rows.length : saved?.length}>
        <div className="ml-auto flex items-center gap-2">
          <div className="segmented" role="group" aria-label={COPY.term.topics}>
            {(['week', 'saved'] as const).map((k) => (
              <button
                key={k}
                className={cn(tab === k && 'active')}
                aria-pressed={tab === k}
                onClick={() => setTab(k)}
              >
                {T.tabs[k]}
              </button>
            ))}
          </div>
          {tab === 'week' && data && data.weeks.length > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
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
        </div>
      </PageHeader>

      {error && !data && <div className="empty-state">{error}</div>}
      {error && data && <div className="px-(--gutter) py-2 text-xs">{error}</div>}

      {tab === 'week' && (
        <>
          {!data && !error && <div className="empty-state">{COPY.term.loading}</div>}
          {data && current === null && <div className="empty-state">{T.empty}</div>}
          {data && current !== null && rows.length === 0 && (
            <div className="empty-state">{T.emptyWeek}</div>
          )}
          {data && data.method && rows.length > 0 && (
            <div className="px-(--gutter) py-2 text-xs text-muted-foreground">
              <span className="mono">{T.method(data.method, data.k)}</span>
            </div>
          )}
          {groups.map((g) => (
            <section key={g.key} className="feed-group">
              <h2>{g.title}</h2>
              <ul className="m-0 list-none p-0">
                {g.items.map((t) => (
                  <TopicItem
                    key={t.id}
                    topic={t}
                    busy={busyId === t.id}
                    onToggleSave={() =>
                      void act(t.id, () =>
                        t.saved ? api.unsaveTopic(t.id) : api.saveTopic(t.id, t.week)
                      )
                    }
                    onAnswer={(parentId, verdict) =>
                      void act(t.id, () => api.topicParentFeedback(t.id, t.week, parentId, verdict))
                    }
                    onAttach={(parentId) =>
                      void act(t.id, () => api.topicSetParent(t.id, t.week, parentId))
                    }
                    onRelease={() => void act(t.id, () => api.topicSetParent(t.id, t.week, null))}
                  />
                ))}
              </ul>
            </section>
          ))}
          {canMore && (
            <div className="flex items-center justify-center gap-2 px-(--gutter) py-3">
              <Button
                size="sm"
                variant="outline"
                disabled={loadingMore}
                onClick={() => {
                  setLoadingMore(true)
                  void load(
                    undefined,
                    Math.min(rows.length + Math.max(k, 1), data.max_limit)
                  ).finally(() => setLoadingMore(false))
                }}
              >
                {loadingMore ? COPY.term.loading : T.more(rows.length, data.max_limit)}
              </Button>
            </div>
          )}
        </>
      )}

      {tab === 'saved' && (
        <>
          {!saved && !error && <div className="empty-state">{COPY.term.loading}</div>}
          {saved && saved.length === 0 && <div className="empty-state">{T.savedEmpty}</div>}
          {saved && saved.length > 0 && (
            <SavedList items={saved} onUnsave={(id) => void act(id, () => api.unsaveTopic(id))} />
          )}
        </>
      )}
    </div>
  )
}

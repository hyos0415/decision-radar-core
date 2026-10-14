import { useEffect, useEffectEvent, useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, X } from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
  SUCCESS_OUTCOMES,
  type ActionResult,
  type AdrDraft,
  type InboxDetail,
  type StaleAnswer
} from '@renderer/lib/api'
import { EditDialog } from './EditDialog'
import { Markdown } from './Markdown'
import { STATE_ICON, STATE_LABEL, waitText } from '@renderer/lib/inbox'
import { shortcutKey } from '@renderer/lib/shortcuts'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle
} from './ui/alert-dialog'
import { Button } from './ui/button'
import { Status } from './Status'
import { COPY } from '../../../copy'

type StaleState = { kind: 'approve' | 'edit'; draft?: AdrDraft; changes: string[] }

const STALE_BUTTONS: {
  answer: StaleAnswer
  label: string
  key: string
  variant: 'default' | 'outline' | 'ghost'
}[] = [
  { answer: 'continue', label: COPY.item.stale.continue, key: '1', variant: 'default' },
  { answer: 'rejudge', label: COPY.item.stale.rejudge, key: '2', variant: 'outline' },
  { answer: 'discard', label: COPY.item.stale.discard, key: '3', variant: 'ghost' }
]

export function ItemDetail({
  detail,
  onResolved,
  onClose,
  onPrev,
  onNext,
  active
}: {
  detail: InboxDetail
  onResolved: () => void
  onClose: () => void
  onPrev: (() => void) | null
  onNext: (() => void) | null
  // false while the next item loads — the old detail stays visible but must not take keys
  active: boolean
}): React.JSX.Element {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [checkErrors, setCheckErrors] = useState<ActionResult | null>(null)
  const [stale, setStale] = useState<StaleState | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)

  const has = (a: 'approve' | 'edit' | 'reject'): boolean => detail.actions.includes(a)
  const stateLabel = STATE_LABEL[detail.state]

  // Returns the result so the edit dialog can show edit_errors.
  // Outside the dialog (plain approve — R18 affected, or after a stale answer) they go in the action bar.
  async function run(
    kind: 'approve' | 'edit' | 'reject',
    draft?: AdrDraft,
    answer: StaleAnswer | null = null
  ): Promise<ActionResult | null> {
    setBusy(true)
    setNotice(null)
    setCheckErrors(null)
    try {
      const res =
        kind === 'approve'
          ? await api.approve(detail.id, answer)
          : kind === 'edit'
            ? await api.edit(detail.id, draft as AdrDraft, answer)
            : await api.reject(detail.id)
      if (SUCCESS_OUTCOMES.includes(res.outcome)) {
        toast(res.message ?? COPY.item.done)
        setEditOpen(false)
        onResolved()
      } else if (res.outcome === 'stale') {
        setEditOpen(false)
        setStale({
          kind: kind === 'edit' ? 'edit' : 'approve',
          draft,
          changes: res.changes ?? []
        })
      } else if (res.outcome === 'edit_errors') {
        if (!editOpen) setCheckErrors(res)
      } else {
        setNotice(res.message ?? (res.outcome === 'blocked' ? COPY.item.blocked : COPY.item.error))
      }
      return res
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  // a approve · e edit dialog · r reject confirm; while stale is up, 1 continue · 2 rejudge · 3 discard.
  // No button, no key (R8). Nothing while busy.
  const onKey = useEffectEvent((e: KeyboardEvent): void => {
    const k = shortcutKey(e)
    if (!k || !active || busy) return
    let act: (() => void) | null = null
    if (stale) {
      const b = STALE_BUTTONS.find((x) => x.key === k)
      if (b) act = () => void run(stale.kind, stale.draft, b.answer)
    } else if (k === 'a' && has('approve')) {
      act = () => void run('approve')
    } else if (k === 'e' && has('edit')) {
      act = () => setEditOpen(true)
    } else if (k === 'r' && has('reject')) {
      act = () => setRejectOpen(true)
    }
    if (!act) return
    e.preventDefault()
    act()
  })
  useEffect(() => {
    const listener = (e: KeyboardEvent): void => onKey(e)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  return (
    <>
      <div className="panel-head">
        <span className="eyebrow">INBOX</span>
        <button
          className="icon-button"
          onClick={onPrev ?? undefined}
          disabled={!onPrev}
          aria-label={COPY.item.prev}
          title={COPY.item.prevHint}
        >
          <ChevronUp size={15} />
        </button>
        <button
          className="icon-button"
          onClick={onNext ?? undefined}
          disabled={!onNext}
          aria-label={COPY.item.next}
          title={COPY.item.nextHint}
        >
          <ChevronDown size={15} />
        </button>
        <span className="detail-close-wrap">
          <span className="esc-hint">Esc</span>
          <button className="icon-button" onClick={onClose} aria-label={COPY.item.close}>
            <X size={16} />
          </button>
        </span>
      </div>

      <div className="panel-scroll">
        <header className="detail-header">
          <h2 className="panel-title">{detail.title}</h2>
          <div className="detail-meta">
            <span className="tag mono">{detail.kind}</span>
            {stateLabel && <Status status={STATE_ICON[detail.state]} label={stateLabel} />}
            <span className="mono">{detail.technology}</span>
            {detail.affected.length > 0 && (
              <span className="mono">
                {detail.affected.map((a) => `${a.repo}/${a.component}`).join(', ')}
              </span>
            )}
            <span>{waitText(detail.waiting_days)}</span>
            <span>{detail.number_note || COPY.item.numberNote}</span>
          </div>
        </header>

        {detail.changes.length > 0 && (
          <div className="callout">
            <AlertTriangle size={16} />
            <div>
              <strong>{COPY.item.changes}</strong>
              <ul>
                {detail.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        <Markdown>{detail.markdown}</Markdown>
      </div>

      <div className="action-bar">
        {stale && (
          <div className="inline-confirm" role="alert">
            <AlertTriangle size={17} />
            <span>{COPY.item.stale.prompt}</span>
            <div className="action-buttons">
              {STALE_BUTTONS.map((b) => (
                <Button
                  key={b.answer}
                  size="sm"
                  variant={b.variant}
                  disabled={busy}
                  onClick={() => run(stale.kind, stale.draft, b.answer)}
                >
                  {b.label}
                  <kbd className="key-hint" aria-hidden>
                    {b.key}
                  </kbd>
                </Button>
              ))}
            </div>
            {stale.changes.length > 0 && (
              <ul className="w-full list-disc pl-5 text-xs text-muted-foreground">
                {stale.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        {notice && (
          <div className="inline-notice" role="status">
            {notice}
          </div>
        )}
        {checkErrors && (
          <div role="alert" className="w-full rounded-md border bg-muted p-3 text-xs">
            <div className="mb-1 font-medium">{checkErrors.message ?? COPY.item.notSaved}</div>
            <ul className="list-disc pl-4">
              {checkErrors.errors?.map((e, i) => (
                <li key={i} className="font-mono">
                  {e}
                </li>
              ))}
            </ul>
          </div>
        )}
        {!stale && (
          <div className="action-buttons right">
            {has('approve') && (
              <Button disabled={busy} onClick={() => run('approve')}>
                {COPY.term.approve}
                <kbd className="key-hint" aria-hidden>
                  A
                </kbd>
              </Button>
            )}
            {has('edit') && (
              <Button variant="outline" disabled={busy} onClick={() => setEditOpen(true)}>
                {COPY.term.edit}
                <kbd className="key-hint" aria-hidden>
                  E
                </kbd>
              </Button>
            )}
            {has('reject') && (
              <Button variant="ghost" disabled={busy} onClick={() => setRejectOpen(true)}>
                {COPY.term.reject}
                <kbd className="key-hint" aria-hidden>
                  R
                </kbd>
              </Button>
            )}
          </div>
        )}
      </div>

      {editOpen && (
        <EditDialog
          open
          onOpenChange={setEditOpen}
          draft={detail.draft}
          busy={busy}
          submit={(d) => run('edit', d)}
        />
      )}

      <AlertDialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <AlertDialogContent>
          <AlertDialogTitle>{COPY.item.rejectConfirm.title}</AlertDialogTitle>
          <AlertDialogDescription>{COPY.item.rejectConfirm.body}</AlertDialogDescription>
          <div className="flex justify-end gap-2">
            <AlertDialogCancel>{COPY.term.cancel}</AlertDialogCancel>
            <AlertDialogAction onClick={() => run('reject')}>{COPY.term.reject}</AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

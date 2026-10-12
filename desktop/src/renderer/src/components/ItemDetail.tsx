import { useState } from 'react'
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
import { STATE_LABEL, stateIcon, waitText } from '@renderer/lib/inbox'
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

type StaleState = { kind: 'approve' | 'edit'; draft?: AdrDraft; changes: string[] }

const STALE_BUTTONS: {
  answer: StaleAnswer
  label: string
  variant: 'default' | 'outline' | 'ghost'
}[] = [
  { answer: 'continue', label: '계속', variant: 'default' },
  { answer: 'rejudge', label: '다시 판정', variant: 'outline' },
  { answer: 'discard', label: '버림', variant: 'ghost' }
]

export function ItemDetail({
  detail,
  onResolved,
  onClose,
  onPrev,
  onNext
}: {
  detail: InboxDetail
  onResolved: () => void
  onClose: () => void
  onPrev: (() => void) | null
  onNext: (() => void) | null
}): React.JSX.Element {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [stale, setStale] = useState<StaleState | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)

  const has = (a: 'approve' | 'edit' | 'reject'): boolean => detail.actions.includes(a)
  const stateLabel = STATE_LABEL[detail.state]

  // Returns the result so the edit dialog can show edit_errors.
  async function run(
    kind: 'approve' | 'edit' | 'reject',
    draft?: AdrDraft,
    answer: StaleAnswer | null = null
  ): Promise<ActionResult | null> {
    setBusy(true)
    setNotice(null)
    try {
      const res =
        kind === 'approve'
          ? await api.approve(detail.id, answer)
          : kind === 'edit'
            ? await api.edit(detail.id, draft as AdrDraft, answer)
            : await api.reject(detail.id)
      if (SUCCESS_OUTCOMES.includes(res.outcome)) {
        toast(res.message ?? '처리했다.')
        setEditOpen(false)
        onResolved()
      } else if (res.outcome === 'stale') {
        setEditOpen(false)
        setStale({
          kind: kind === 'edit' ? 'edit' : 'approve',
          draft,
          changes: res.changes ?? []
        })
      } else if (res.outcome !== 'edit_errors') {
        setNotice(res.message ?? (res.outcome === 'blocked' ? '막혔다.' : '오류가 났다.'))
      }
      return res
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e))
      return null
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="overlay-panel" aria-label="Inbox 항목 상세">
      <div className="panel-head">
        <span className="eyebrow">INBOX</span>
        <button
          className="icon-button"
          onClick={onPrev ?? undefined}
          disabled={!onPrev}
          aria-label="이전 항목"
        >
          <ChevronUp size={15} />
        </button>
        <button
          className="icon-button"
          onClick={onNext ?? undefined}
          disabled={!onNext}
          aria-label="다음 항목"
        >
          <ChevronDown size={15} />
        </button>
        <span className="detail-close-wrap">
          <span className="esc-hint">Esc</span>
          <button className="icon-button" onClick={onClose} aria-label="상세 닫기">
            <X size={16} />
          </button>
        </span>
      </div>

      <div className="panel-scroll">
        <header className="detail-header">
          <h2 className="panel-title">{detail.title}</h2>
          <div className="detail-meta">
            <span className="tag mono">{detail.kind}</span>
            {stateLabel && <Status status={stateIcon(detail.state)} label={stateLabel} />}
            <span className="mono">{detail.technology}</span>
            {detail.affected.length > 0 && (
              <span className="mono">
                {detail.affected.map((a) => `${a.repo}/${a.component}`).join(', ')}
              </span>
            )}
            <span>{waitText(detail.waiting_days)}</span>
            <span>{detail.number_note || '번호는 저장할 때 정해짐'}</span>
          </div>
        </header>

        {detail.changes.length > 0 && (
          <div className="callout">
            <AlertTriangle size={16} />
            <div>
              <strong>대기 중 바뀐 점</strong>
              <ul>
                {detail.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          </div>
        )}

        <div className="document">{detail.markdown}</div>
      </div>

      <div className="action-bar">
        {stale && (
          <div className="inline-confirm" role="alert">
            <AlertTriangle size={17} />
            <span>대기하는 동안 기록이 바뀌었다. 어떻게 할까?</span>
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
        {!stale && (
          <div className="action-buttons right">
            {has('approve') && (
              <Button disabled={busy} onClick={() => run('approve')}>
                승인
              </Button>
            )}
            {has('edit') && (
              <Button variant="outline" disabled={busy} onClick={() => setEditOpen(true)}>
                고쳐서 승인
              </Button>
            )}
            {has('reject') && (
              <Button variant="ghost" disabled={busy} onClick={() => setRejectOpen(true)}>
                거부
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
          <AlertDialogTitle>이 항목을 거부할까?</AlertDialogTitle>
          <AlertDialogDescription>거부하면 목록에서 빠진다.</AlertDialogDescription>
          <div className="flex justify-end gap-2">
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction onClick={() => run('reject')}>거부</AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}

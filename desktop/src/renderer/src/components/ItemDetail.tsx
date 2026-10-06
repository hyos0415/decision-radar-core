import { useState } from 'react'
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
import { STATE_LABEL, waitText } from '@renderer/lib/inbox'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle
} from './ui/alert-dialog'
import { Badge } from './ui/badge'
import { Button } from './ui/button'

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
  onResolved
}: {
  detail: InboxDetail
  onResolved: () => void
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
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <h1 className="text-base leading-snug font-semibold">{detail.title}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
          <Badge variant="outline">{detail.kind}</Badge>
          {stateLabel && <Badge variant="secondary">{stateLabel}</Badge>}
          <span className="font-mono">{detail.technology}</span>
          {detail.affected.length > 0 && (
            <>
              <span>·</span>
              <span className="font-mono">
                {detail.affected.map((a) => `${a.repo}/${a.component}`).join(', ')}
              </span>
            </>
          )}
          <span>·</span>
          <span>{waitText(detail.waiting_days)}</span>
          <span>·</span>
          <span>{detail.number_note || '번호는 저장할 때 정해짐'}</span>
        </div>

        {detail.changes.length > 0 && (
          <div className="mt-4 rounded-md border p-3">
            <div className="mb-1.5 text-xs font-medium">대기 중 바뀐 점</div>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
              {detail.changes.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        )}

        <pre className="mt-4 overflow-x-auto rounded-md border bg-muted p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap">
          {detail.markdown}
        </pre>
      </div>

      <div className="shrink-0 border-t bg-background px-6 py-3">
        {stale && (
          <div className="mb-3 rounded-md border p-3" role="alert">
            <div className="text-xs font-medium">대기하는 동안 기록이 바뀌었다. 어떻게 할까?</div>
            {stale.changes.length > 0 && (
              <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                {stale.changes.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            )}
            <div className="mt-2.5 flex gap-2">
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
          </div>
        )}
        {notice && (
          <div className="mb-3 rounded-md border bg-muted px-3 py-2 text-xs" role="status">
            {notice}
          </div>
        )}
        <div className="flex items-center gap-2">
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
    </div>
  )
}

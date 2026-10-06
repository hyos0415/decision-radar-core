import { useState } from 'react'
import type { ActionResult, AdrDraft } from '@renderer/lib/api'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './ui/dialog'
import { Input, Textarea } from './ui/input'
import { Label } from './ui/label'

const EMPTY: AdrDraft = {
  title: '',
  technology: '',
  use: '',
  affected: [],
  evidence: [],
  context: '',
  decision: '',
  related: [],
  research_note: [],
  revisit_when: []
}

function Field({ label, children }: { label: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <div className="grid gap-1">
      <Label>{label}</Label>
      {children}
    </div>
  )
}

export function EditDialog({
  open,
  onOpenChange,
  draft,
  busy,
  submit
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  draft: AdrDraft | null
  busy: boolean
  submit: (draft: AdrDraft) => Promise<ActionResult | null>
}): React.JSX.Element {
  const base = draft ?? EMPTY
  const [title, setTitle] = useState(base.title)
  const [use, setUse] = useState(base.use)
  const [context, setContext] = useState(base.context)
  const [decision, setDecision] = useState(base.decision)
  const [note, setNote] = useState(base.research_note.join('\n'))
  const [lists, setLists] = useState(
    JSON.stringify(
      {
        affected: base.affected,
        evidence: base.evidence,
        related: base.related,
        revisit_when: base.revisit_when
      },
      null,
      2
    )
  )
  const [errors, setErrors] = useState<string[]>([])

  async function onSubmit(): Promise<void> {
    let parsed: Pick<AdrDraft, 'affected' | 'evidence' | 'related' | 'revisit_when'>
    try {
      parsed = JSON.parse(lists)
    } catch (e) {
      setErrors([`JSON 형식이 맞지 않다: ${e instanceof Error ? e.message : String(e)}`])
      return
    }
    const next: AdrDraft = {
      title,
      technology: base.technology,
      use,
      context,
      decision,
      research_note: note
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean),
      affected: parsed.affected ?? [],
      evidence: parsed.evidence ?? [],
      related: parsed.related ?? [],
      revisit_when: parsed.revisit_when ?? []
    }
    setErrors([])
    const res = await submit(next)
    if (res?.outcome === 'edit_errors') {
      setErrors(res.errors?.length ? res.errors : [res.message ?? '검사를 통과하지 못했다.'])
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogTitle>고쳐서 승인</DialogTitle>
        <DialogDescription>
          상태와 번호는 고칠 수 없다. 저장할 때 정해진다.
        </DialogDescription>
        <div className="-mx-1 grid flex-1 gap-3 overflow-y-auto px-1 pb-1">
          <Field label="제목">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label="기술 (읽기 전용)">
            <Input value={base.technology} readOnly disabled className="font-mono" />
          </Field>
          <Field label="쓰는 곳 (use)">
            <Input value={use} onChange={(e) => setUse(e.target.value)} />
          </Field>
          <Field label="배경 (context)">
            <Textarea rows={4} value={context} onChange={(e) => setContext(e.target.value)} />
          </Field>
          <Field label="결정 (decision)">
            <Textarea rows={4} value={decision} onChange={(e) => setDecision(e.target.value)} />
          </Field>
          <Field label="조사 메모 (한 줄에 하나)">
            <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <Field label="목록 칸 (JSON: affected · evidence · related · revisit_when)">
            <Textarea
              rows={10}
              spellCheck={false}
              className="font-mono text-xs"
              value={lists}
              onChange={(e) => setLists(e.target.value)}
            />
          </Field>
        </div>
        {errors.length > 0 && (
          <div role="alert" className="rounded-md border bg-muted p-3 text-xs">
            <div className="mb-1 font-medium">저장하지 않았다. 아래를 고쳐 다시 시도한다.</div>
            <ul className="list-disc pl-4">
              {errors.map((e, i) => (
                <li key={i} className="font-mono">
                  {e}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            취소
          </Button>
          <Button onClick={onSubmit} disabled={busy}>
            {busy ? '저장 중…' : '고쳐서 승인'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

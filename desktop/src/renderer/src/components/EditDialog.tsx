import { useState } from 'react'
import type { ActionResult, AdrDraft } from '@renderer/lib/api'
import { Button } from './ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './ui/dialog'
import { Input, Textarea } from './ui/input'
import { Label } from './ui/label'
import { COPY } from '../../../copy'

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

function Field({
  label,
  children
}: {
  label: string
  children: React.ReactNode
}): React.JSX.Element {
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
      setErrors([COPY.edit.badJson(e instanceof Error ? e.message : String(e))])
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
      setErrors(res.errors?.length ? res.errors : [res.message ?? COPY.edit.checkFailed])
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogTitle>{COPY.term.edit}</DialogTitle>
        <DialogDescription>{COPY.edit.note}</DialogDescription>
        <div className="-mx-1 grid flex-1 gap-3 overflow-y-auto px-1 pb-1">
          <Field label={COPY.edit.title}>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label={COPY.edit.technology}>
            <Input value={base.technology} readOnly disabled className="font-mono" />
          </Field>
          <Field label={COPY.edit.use}>
            <Input value={use} onChange={(e) => setUse(e.target.value)} />
          </Field>
          <Field label={COPY.edit.context}>
            <Textarea rows={4} value={context} onChange={(e) => setContext(e.target.value)} />
          </Field>
          <Field label={COPY.edit.decision}>
            <Textarea rows={4} value={decision} onChange={(e) => setDecision(e.target.value)} />
          </Field>
          <Field label={COPY.edit.researchNote}>
            <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <Field label={COPY.edit.lists}>
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
            <div className="mb-1 font-medium">{COPY.edit.notSaved}</div>
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
            {COPY.term.cancel}
          </Button>
          <Button onClick={onSubmit} disabled={busy}>
            {busy ? COPY.edit.saving : COPY.term.edit}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

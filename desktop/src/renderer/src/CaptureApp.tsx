import { useEffect, useRef, useState } from 'react'
import { api, setBaseUrl, type CaptureResult } from './lib/api'
import { isMac } from './lib/format'
import { Button } from './components/ui/button'
import { COPY } from '../../copy'

type Backend = Awaited<ReturnType<typeof window.radar.getBackend>>
const drag = { WebkitAppRegion: 'drag' } as React.CSSProperties

export function CaptureApp(): React.JSX.Element {
  const [backend, setBackend] = useState<Backend>({ status: 'starting' })
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<CaptureResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ta = useRef<HTMLTextAreaElement>(null)
  const busyRef = useRef(false)

  useEffect(() => {
    busyRef.current = busy
  }, [busy])

  const ready = backend.status === 'ready' && !!backend.baseUrl
  if (ready) setBaseUrl(backend.baseUrl!)

  useEffect(() => {
    // the window is transparent so the rounded card + shadow show through
    document.documentElement.style.background = 'transparent'
    document.body.style.background = 'transparent'
    window.radar.getBackend().then(setBackend)
    const off1 = window.radar.onBackendStatus(setBackend)
    const off2 = window.radar.onCaptureShown(() => {
      if (!busyRef.current) {
        setResult(null)
        setError(null)
      }
      ta.current?.focus()
    })
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') window.radar.hideCapture()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      off1()
      off2()
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  async function submit(): Promise<void> {
    if (!ready || busy || !text.trim()) return
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      const res = await api.capture(text)
      setResult(res)
      if (res.outcome === 'awaiting') {
        setText('')
        window.radar.notifyChanged(res.item ?? undefined)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
      ta.current?.focus()
    }
  }

  const mod = isMac() ? '⌘' : 'Ctrl'

  return (
    <div className="h-full p-3">
      <div className="flex h-full flex-col overflow-hidden rounded-xl border bg-background shadow-[0_8px_30px_rgba(0,0,0,0.14)]">
        <div className="shrink-0 px-5 pt-4 pb-2 text-[13px] font-medium" style={drag}>
          {COPY.capture.heading}
        </div>
        <textarea
          ref={ta}
          autoFocus
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault()
              void submit()
            }
          }}
          placeholder={COPY.capture.placeholder}
          spellCheck={false}
          className="mx-5 min-h-0 flex-1 resize-none rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed outline-none placeholder:text-muted-foreground focus-visible:border-ring disabled:opacity-60"
        />
        <div className="flex min-h-5 shrink-0 items-center gap-2 px-5 pt-2 text-xs">
          {busy && <span className="text-muted-foreground">{COPY.capture.working}</span>}
          {!busy && error && <span>{error}</span>}
          {!busy && result?.outcome === 'awaiting' && (
            <>
              <span>{result.message ?? COPY.capture.added}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  window.radar.navigate({ view: 'inbox', id: result.item ?? undefined })
                }
              >
                {COPY.capture.viewInInbox}
              </Button>
            </>
          )}
          {!busy && result?.outcome === 'linked' && (
            <>
              <span>{COPY.capture.linked((result.link_candidates ?? []).join(', '))}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  window.radar.navigate({ view: 'decisions', id: result.link_candidates?.[0] })
                }
              >
                {COPY.capture.viewInDecisions}
              </Button>
            </>
          )}
          {!busy && result?.outcome === 'no_draft' && (
            <span>{result.message ?? COPY.capture.noDraft}</span>
          )}
        </div>
        <div className="flex shrink-0 items-center justify-between px-5 pt-2 pb-4">
          <span className="text-[11px] text-muted-foreground">{COPY.capture.hint(mod)}</span>
          <Button disabled={busy || !ready || !text.trim()} onClick={() => void submit()}>
            {COPY.capture.submit}
          </Button>
        </div>
      </div>
    </div>
  )
}

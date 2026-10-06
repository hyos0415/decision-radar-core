import { useEffect, useRef, useState } from 'react'
import { api, setBaseUrl, type CaptureResult } from './lib/api'
import { isMac } from './lib/format'
import { Button } from './components/ui/button'

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
        window.radar.notifyChanged(res.item)
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
          대화 붙여넣기 → 결정 기록 초안
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
          placeholder="조사 대화를 붙여 넣는다…"
          spellCheck={false}
          className="mx-5 min-h-0 flex-1 resize-none rounded-md border bg-muted/40 p-3 font-mono text-xs leading-relaxed outline-none placeholder:text-muted-foreground focus-visible:border-ring disabled:opacity-60"
        />
        <div className="flex min-h-5 shrink-0 items-center gap-2 px-5 pt-2 text-xs">
          {busy && <span className="text-muted-foreground">초안 만드는 중…</span>}
          {!busy && error && <span>{error}</span>}
          {!busy && result?.outcome === 'awaiting' && (
            <>
              <span>{result.message ?? 'Inbox에 추가됨'}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => window.radar.navigate({ view: 'inbox', id: result.item })}
              >
                Inbox에서 보기
              </Button>
            </>
          )}
          {!busy && result?.outcome === 'linked' && (
            <>
              <span>이미 있는 결정과 같다: {(result.link_candidates ?? []).join(', ')}</span>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  window.radar.navigate({ view: 'decisions', id: result.link_candidates?.[0] })
                }
              >
                Decisions에서 보기
              </Button>
            </>
          )}
          {!busy && result?.outcome === 'no_draft' && (
            <span>{result.message ?? '초안이 나오지 않았다'}</span>
          )}
        </div>
        <div className="flex shrink-0 items-center justify-between px-5 pt-2 pb-4">
          <span className="text-[11px] text-muted-foreground">{mod}+Enter 보내기 · Esc 닫기</span>
          <Button disabled={busy || !ready || !text.trim()} onClick={() => void submit()}>
            초안 만들기
          </Button>
        </div>
      </div>
    </div>
  )
}

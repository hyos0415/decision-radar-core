import { Component, type ErrorInfo, type ReactNode } from 'react'
import { COPY } from '../../copy'

// One boundary for both windows (HYO-108): a render crash shows this instead of a blank window.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error): { error: Error } {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[renderer] crashed', error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 p-6 text-center">
        <strong>{COPY.app.crashTitle}</strong>
        <p className="max-w-md break-words text-xs text-muted-foreground">
          {error.message || String(error)}
        </p>
        <button
          type="button"
          className="rounded-md border px-3 py-1.5 text-sm"
          onClick={() => location.reload()}
        >
          {COPY.app.reload}
        </button>
      </div>
    )
  }
}

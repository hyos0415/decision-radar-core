import type { BackendStatus, NavTarget, RadarInfo } from '../shared'

declare global {
  interface Window {
    radar: {
      getBackend(): Promise<BackendStatus>
      onBackendStatus(cb: (s: BackendStatus) => void): () => void
      getInfo(): Promise<RadarInfo>
      openCapture(): Promise<void>
      hideCapture(): void
      navigate(t: NavTarget): void
      notifyChanged(item?: string): void
      onNavigate(cb: (t: NavTarget) => void): () => void
      onCaptureShown(cb: () => void): () => void
      onDataChanged(cb: () => void): () => void
    }
  }
}

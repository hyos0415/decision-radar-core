import type { BackendStatus, NavTarget, RadarInfo, ThemeSource } from '../shared'

declare global {
  interface Window {
    radar: {
      getBackend(): Promise<BackendStatus>
      onBackendStatus(cb: (s: BackendStatus) => void): () => void
      getInfo(): Promise<RadarInfo>
      getTheme(): Promise<ThemeSource>
      setTheme(t: ThemeSource): Promise<ThemeSource>
      openCapture(seed?: string): Promise<void>
      hideCapture(): void
      navigate(t: NavTarget): void
      notifyChanged(item?: string): void
      onNavigate(cb: (t: NavTarget) => void): () => void
      onCaptureShown(cb: (seed: string | null) => void): () => void
      onDataChanged(cb: () => void): () => void
    }
  }
}

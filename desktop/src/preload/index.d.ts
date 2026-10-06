import type { BackendStatus } from '../shared'

declare global {
  interface Window {
    radar: {
      getBackend(): Promise<BackendStatus>
      onBackendStatus(cb: (s: BackendStatus) => void): () => void
    }
  }
}

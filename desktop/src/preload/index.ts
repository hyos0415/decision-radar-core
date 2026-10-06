import { contextBridge, ipcRenderer } from 'electron'
import type { BackendStatus } from '../shared'

const radar = {
  getBackend: (): Promise<BackendStatus> => ipcRenderer.invoke('backend:get'),
  onBackendStatus: (cb: (s: BackendStatus) => void): (() => void) => {
    const handler = (_: unknown, s: BackendStatus): void => cb(s)
    ipcRenderer.on('backend:status', handler)
    return () => ipcRenderer.removeListener('backend:status', handler)
  }
}

contextBridge.exposeInMainWorld('radar', radar)

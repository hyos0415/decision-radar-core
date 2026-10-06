import { contextBridge, ipcRenderer } from 'electron'
import type { BackendStatus, NavTarget, RadarInfo } from '../shared'

function listen<T>(channel: string, cb: (v: T) => void): () => void {
  const handler = (_: unknown, v: T): void => cb(v)
  ipcRenderer.on(channel, handler)
  return () => ipcRenderer.removeListener(channel, handler)
}

const radar = {
  getBackend: (): Promise<BackendStatus> => ipcRenderer.invoke('backend:get'),
  onBackendStatus: (cb: (s: BackendStatus) => void): (() => void) => listen('backend:status', cb),
  getInfo: (): Promise<RadarInfo> => ipcRenderer.invoke('info:get'),
  openCapture: (): Promise<void> => ipcRenderer.invoke('capture:open'),
  hideCapture: (): void => ipcRenderer.send('capture:hide'),
  navigate: (t: NavTarget): void => ipcRenderer.send('nav:go', t),
  notifyChanged: (item?: string): void => ipcRenderer.send('data:changed', item ?? null),
  onNavigate: (cb: (t: NavTarget) => void): (() => void) => listen('nav', cb),
  onCaptureShown: (cb: () => void): (() => void) => listen('capture:shown', cb),
  onDataChanged: (cb: () => void): (() => void) => listen('data:changed', cb)
}

contextBridge.exposeInMainWorld('radar', radar)

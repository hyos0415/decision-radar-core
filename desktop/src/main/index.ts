import { app, BrowserWindow, ipcMain, shell } from 'electron'
import { writeFileSync } from 'fs'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'
import { getStatus, onStatus, startBackend, stopBackend } from './backend'
import { createTray, destroyTray, setTrayState } from './tray'
import {
  createCaptureWindow,
  hideCapture,
  isShortcutOk,
  registerCaptureShortcut,
  shortcut,
  showCapture,
  unregisterCaptureShortcut
} from './capture'
import { markKnown, pollNow, postRun, startPoller, stopPoller } from './poller'
import type { NavTarget } from '../shared'

let win: BrowserWindow | null = null
let quitting = false

function quitApp(): void {
  quitting = true
  app.quit()
}

function showWindow(): void {
  if (!win) return
  if (win.isMinimized()) win.restore()
  win.show()
  win.focus()
}

function navigate(t: NavTarget): void {
  hideCapture()
  showWindow()
  if (win && !win.isDestroyed()) win.webContents.send('nav', t)
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1200,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: 'Decision Radar',
    backgroundColor: '#ffffff',
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  win.on('ready-to-show', () => win?.show())
  win.on('close', (e) => {
    if (!quitting) {
      e.preventDefault()
      win?.hide()
    }
  })
  win.webContents.setWindowOpenHandler((d) => {
    shell.openExternal(d.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    win.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }

  const shot = process.env.RADAR_SCREENSHOT
  if (shot) scheduleScreenshot(win, shot)
}

// Dev verification hook: RADAR_SCREENSHOT=<path> saves a PNG once the backend is ready, then quits.
function scheduleScreenshot(w: BrowserWindow, path: string): void {
  const delay = Number(process.env.RADAR_SCREENSHOT_DELAY_MS || 2000)
  const poll = setInterval(async () => {
    if (getStatus().status === 'starting' || w.webContents.isLoading()) return
    clearInterval(poll)
    await new Promise((r) => setTimeout(r, delay))
    try {
      const img = await w.webContents.capturePage()
      writeFileSync(path, img.toPNG())
      console.log(`[screenshot] ${path}`)
    } catch (e) {
      console.error('[screenshot] failed', e)
    }
    quitApp()
  }, 300)
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', showWindow)

  app.whenReady().then(() => {
    if (process.platform === 'win32') app.setAppUserModelId('decision-radar')

    ipcMain.handle('backend:get', () => getStatus())
    onStatus((s) => {
      if (win && !win.isDestroyed()) win.webContents.send('backend:status', s)
    })

    ipcMain.handle('info:get', () => ({
      shortcut,
      shortcutOk: isShortcutOk(),
      platform: process.platform
    }))
    ipcMain.handle('capture:open', () => showCapture())
    ipcMain.on('capture:hide', () => hideCapture())
    ipcMain.on('nav:go', (_e, t: NavTarget) => {
      if (t && ['inbox', 'decisions', 'runs'].includes(t.view)) navigate(t)
    })
    ipcMain.on('data:changed', (_e, item: string | null) => {
      if (item) markKnown(item)
      if (win && !win.isDestroyed()) win.webContents.send('data:changed')
      void pollNow()
    })

    createWindow()
    createCaptureWindow()
    createTray({
      getWindow: () => win,
      onQuit: quitApp,
      onCapture: showCapture,
      onRun: (flow) => {
        void postRun(flow).then(() => pollNow())
      }
    })
    setTrayState({ shortcutOk: registerCaptureShortcut() })

    let polling = false
    const maybeStartPoller = (): void => {
      if (polling || getStatus().status !== 'ready') return
      polling = true
      startPoller({
        onClick: navigate,
        onPending: (n) => setTrayState({ pending: n }),
        onChanged: () => {
          if (win && !win.isDestroyed()) win.webContents.send('data:changed')
        }
      })
    }
    onStatus(maybeStartPoller)
    void startBackend().then(maybeStartPoller)

    app.on('activate', showWindow)
  })

  // Stay resident in the tray when the window is closed.
  app.on('window-all-closed', () => {})
  app.on('before-quit', () => {
    quitting = true
    stopBackend()
  })
  app.on('will-quit', () => {
    unregisterCaptureShortcut()
    stopPoller()
    stopBackend()
    destroyTray()
  })
  process.on('exit', stopBackend)
  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
    process.on(sig, () => {
      stopBackend()
      app.exit(0)
    })
  }
}

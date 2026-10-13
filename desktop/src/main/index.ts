import { app, BrowserWindow, ipcMain, nativeTheme, shell } from 'electron'
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
import { getTheme, loadTheme, setTheme } from './theme'
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

// 링크는 앱 창 안에서 열지 않는다 — http(s)만 시스템 브라우저로 넘기고 나머지는 버린다.
function openExternalSafe(url: string): void {
  try {
    const { protocol } = new URL(url)
    if (protocol === 'http:' || protocol === 'https:') void shell.openExternal(url)
  } catch {
    // 잘못된 URL은 무시한다
  }
}

function guardNavigation(contents: Electron.WebContents): void {
  contents.setWindowOpenHandler((d) => {
    openExternalSafe(d.url)
    return { action: 'deny' }
  })
  contents.on('will-navigate', (e, url) => {
    // 같은 문서 안의 이동(#/capture 같은 hash)은 둔다
    const cur = contents.getURL()
    if (cur && url.split('#')[0] === cur.split('#')[0]) return
    e.preventDefault()
    openExternalSafe(url)
  })
}

// same values as the renderer tokens (main.css --background · --muted-foreground)
function windowColors(): { background: string; symbol: string } {
  return nativeTheme.shouldUseDarkColors
    ? { background: '#0a0a0a', symbol: '#a3a3a3' }
    : { background: '#fafafa', symbol: '#737373' }
}

function titleBarOverlay(): Electron.TitleBarOverlayOptions {
  const c = windowColors()
  return { color: c.background, symbolColor: c.symbol, height: 47 }
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1200,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: 'Decision Radar',
    backgroundColor: windowColors().background,
    show: false,
    autoHideMenuBar: true,
    // flat custom title bar (HYO-101): the 48px header rows are the drag area.
    // macOS keeps its traffic lights; Windows draws its caption buttons over the right of that row.
    titleBarStyle: 'hidden',
    ...(process.platform === 'darwin'
      ? { trafficLightPosition: { x: 16, y: 16 } }
      : { titleBarOverlay: titleBarOverlay() }),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })

  win.on('ready-to-show', () => win?.show())
  // OS theme change or the user's choice (HYO-104) — keep the window and caption buttons in step
  const onTheme = (): void => {
    if (!win || win.isDestroyed()) return
    win.setBackgroundColor(windowColors().background)
    if (process.platform !== 'darwin') win.setTitleBarOverlay(titleBarOverlay())
  }
  nativeTheme.on('updated', onTheme)
  win.on('closed', () => nativeTheme.off('updated', onTheme))
  win.on('close', (e) => {
    if (!quitting) {
      e.preventDefault()
      win?.hide()
    }
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
  // 메인·capture 창 모두에 건다
  app.on('web-contents-created', (_e, contents) => guardNavigation(contents))

  app.whenReady().then(() => {
    if (process.platform === 'win32') app.setAppUserModelId('decision-radar')
    loadTheme()

    ipcMain.handle('backend:get', () => getStatus())
    // 모든 창에 보낸다 — capture 창은 백엔드 준비 전에 만들어지므로 메인 창에만 보내면 계속 '기동 중'에 머문다
    onStatus((s) => {
      for (const w of BrowserWindow.getAllWindows()) {
        if (!w.isDestroyed()) w.webContents.send('backend:status', s)
      }
    })

    ipcMain.handle('info:get', () => ({
      shortcut,
      shortcutOk: isShortcutOk(),
      platform: process.platform
    }))
    ipcMain.handle('theme:get', () => getTheme())
    ipcMain.handle('theme:set', (_e, v: unknown) => setTheme(v))
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
        // the job is accepted at once — tell the window so its run row shows it running
        void postRun(flow).then(() => {
          if (win && !win.isDestroyed()) win.webContents.send('data:changed')
        })
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

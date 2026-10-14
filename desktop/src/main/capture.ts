import { BrowserWindow, globalShortcut, screen } from 'electron'
import { join } from 'path'
import { is } from '@electron-toolkit/utils'
import { postEvent } from './poller'
import { COPY } from '../copy'

export const DEFAULT_SHORTCUT = 'CommandOrControl+Shift+Space'
export const shortcut = process.env.RADAR_CAPTURE_SHORTCUT || DEFAULT_SHORTCUT

const WIDTH = 664 // 640 card + 12px margin each side (room for the shadow)
const HEIGHT = 444 // 420 card + margin
const WIN = process.platform === 'win32'

let cwin: BrowserWindow | null = null
let shownAt = 0
let shortcutOk = false

export function isShortcutOk(): boolean {
  return shortcutOk
}

export function createCaptureWindow(): void {
  if (cwin && !cwin.isDestroyed()) return
  cwin = new BrowserWindow({
    width: WIDTH,
    height: HEIGHT,
    show: false,
    frame: false,
    transparent: true,
    hasShadow: false,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    title: COPY.term.capture,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  })
  cwin.setAlwaysOnTop(true, 'floating')
  cwin.on('blur', () => {
    // ignore the blur that can arrive right while the window is being shown
    if (Date.now() - shownAt < 250) return
    if (process.env.RADAR_CAPTURE_KEEP) return // dev: keep open for screenshots
    hideCapture()
  })
  cwin.on('closed', () => {
    cwin = null
  })
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    void cwin.loadURL(process.env['ELECTRON_RENDERER_URL'] + '#/capture')
  } else {
    void cwin.loadFile(join(__dirname, '../renderer/index.html'), { hash: '/capture' })
  }
}

export function captureWindow(): BrowserWindow | null {
  return cwin && !cwin.isDestroyed() ? cwin : null
}

export function showCapture(): void {
  createCaptureWindow()
  const w = captureWindow()
  if (!w) return
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea
  w.setBounds({
    x: Math.round(area.x + (area.width - WIDTH) / 2),
    y: Math.round(area.y + (area.height - HEIGHT) / 2),
    width: WIDTH,
    height: HEIGHT
  })
  shownAt = Date.now()
  w.show()
  // ponytail: 고정 50ms — 화면 갱신 몇 번 뒤. 첫 프레임이 이보다 늦게 그려지는 느린 기기에선 다시 보일 수 있다
  if (WIN)
    setTimeout(() => {
      if (!w.isDestroyed() && w.isVisible()) w.setOpacity(1)
    }, 50)
  w.focus()
  w.webContents.send('capture:shown')
  postEvent('capture_opened')
}

export function hideCapture(): void {
  const w = captureWindow()
  if (w && w.isVisible()) {
    // 투명하게 숨긴다 — 아니면 Windows에서 다시 열 때 완성된 창이 한 프레임 보였다가 열림 애니메이션이 처음부터 재생돼 깜빡인다.
    // showCapture가 그 프레임이 지난 뒤 되돌린다. macOS는 이 문제가 없다
    if (WIN) w.setOpacity(0)
    w.hide()
  }
}

export function toggleCapture(): void {
  const w = captureWindow()
  if (w && w.isVisible()) hideCapture()
  else showCapture()
}

export function registerCaptureShortcut(): boolean {
  try {
    shortcutOk = globalShortcut.register(shortcut, toggleCapture)
  } catch (e) {
    console.error('[capture] shortcut register threw', e)
    shortcutOk = false
  }
  if (!shortcutOk) console.error(`[capture] 단축키 등록 실패: ${shortcut}`)
  else console.log(`[capture] shortcut ${shortcut}`)
  return shortcutOk
}

export function unregisterCaptureShortcut(): void {
  globalShortcut.unregisterAll()
}

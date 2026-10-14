import { Menu, Tray, nativeImage, type BrowserWindow } from 'electron'
import { COPY } from '../copy'

// 32x32 ring drawn as a BGRA bitmap (2x scale => 16pt). Black on transparent so macOS can template it.
function ringIcon(): Electron.NativeImage {
  const size = 32
  const buf = Buffer.alloc(size * size * 4)
  const c = (size - 1) / 2
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = Math.hypot(x - c, y - c)
      const a = Math.max(0, Math.min(1, 1.5 - Math.abs(d - 11))) * 255
      buf[(y * size + x) * 4 + 3] = a // B,G,R stay 0 (black)
    }
  }
  const img = nativeImage.createFromBitmap(buf, { width: size, height: size, scaleFactor: 2 })
  if (process.platform === 'darwin') img.setTemplateImage(true)
  return img
}

export type TrayActions = {
  getWindow: () => BrowserWindow | null
  onQuit: () => void
  onCapture: () => void
  onRun: (flow: 'detect' | 'revisit') => void
}

let tray: Tray | null = null
let actions: TrayActions | null = null
let pending = 0
let shortcutOk = true

function show(): void {
  const w = actions?.getWindow()
  if (!w) return
  if (w.isMinimized()) w.restore()
  w.show()
  w.focus()
}

function rebuild(): void {
  if (!tray || !actions) return
  const a = actions
  tray.setToolTip(COPY.tray.tooltip(pending))
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: COPY.tray.open, click: show },
      {
        label: shortcutOk ? COPY.term.capture : COPY.tray.captureFailed,
        click: a.onCapture
      },
      { type: 'separator' },
      { label: COPY.tray.run('detect'), click: () => a.onRun('detect') },
      { label: COPY.tray.run('revisit'), click: () => a.onRun('revisit') },
      { type: 'separator' },
      { label: COPY.tray.quit, click: a.onQuit }
    ])
  )
}

export function createTray(a: TrayActions): Tray {
  actions = a
  tray = new Tray(ringIcon())
  tray.on('click', () => {
    const w = a.getWindow()
    if (w && w.isVisible() && w.isFocused()) w.hide()
    else show()
  })
  rebuild()
  return tray
}

export function setTrayState(s: { pending?: number; shortcutOk?: boolean }): void {
  if (s.pending !== undefined) pending = s.pending
  if (s.shortcutOk !== undefined) shortcutOk = s.shortcutOk
  rebuild()
}

export function destroyTray(): void {
  tray?.destroy()
  tray = null
}

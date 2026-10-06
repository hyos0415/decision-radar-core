import { Menu, Tray, nativeImage, type BrowserWindow } from 'electron'

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

let tray: Tray | null = null

export function createTray(getWindow: () => BrowserWindow | null, onQuit: () => void): Tray {
  const show = (): void => {
    const w = getWindow()
    if (!w) return
    if (w.isMinimized()) w.restore()
    w.show()
    w.focus()
  }
  tray = new Tray(ringIcon())
  tray.setToolTip('Decision Radar')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '열기', click: show },
      { type: 'separator' },
      { label: '종료', click: onQuit }
    ])
  )
  tray.on('click', () => {
    const w = getWindow()
    if (w && w.isVisible() && w.isFocused()) w.hide()
    else show()
  })
  return tray
}

export function destroyTray(): void {
  tray?.destroy()
  tray = null
}

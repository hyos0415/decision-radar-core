// npm install 뒤에 Electron 실행 파일을 준비한다 (HYO-146).
// Electron 42부터 바뀐 두 가지를 메운다 (https://www.electronjs.org/docs/latest/breaking-changes 「42.0」):
//   1. 설치 때 실행 파일을 받지 않는다 → electron-vite 5는 path.txt가 없으면 「Electron uninstall」로 멈춘다. 여기서 받는다
//   2. macOS 알림(UNNotification)은 서명된 앱에서만 뜬다 → npm 배포본 서명으로는 failed(UNErrorDomain 1). ad-hoc으로 다시 서명한다
// ELECTRON_SKIP_BINARY_DOWNLOAD=1이면 건너뛴다 (CI는 빌드·lint만 하므로 실행 파일이 필요 없다).
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'

if (process.env.ELECTRON_SKIP_BINARY_DOWNLOAD) {
  console.log('[postinstall] ELECTRON_SKIP_BINARY_DOWNLOAD — Electron 실행 파일을 받지 않는다')
  process.exit(0)
}

const electronDir = dirname(createRequire(import.meta.url).resolve('electron/package.json'))
execFileSync(process.execPath, [join(electronDir, 'install.js')], { stdio: 'inherit' })

if (process.platform === 'darwin') {
  const exe = readFileSync(join(electronDir, 'path.txt'), 'utf-8') // Electron.app/Contents/MacOS/Electron
  const app = join(electronDir, 'dist', exe.split('/Contents/')[0])
  if (!existsSync(app)) throw new Error(`[postinstall] Electron.app이 없다: ${app}`)
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' })
  console.log('[postinstall] Electron.app ad-hoc 서명 — macOS 알림용')
}

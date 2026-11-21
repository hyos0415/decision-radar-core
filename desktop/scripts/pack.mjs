// 설치본 묶기 (HYO-251) — npm run app:pack · npm run app:install(macOS: /Applications에 넣는다)
//
//   RADAR_APP_MODE=real npm run app:install    # 실사용 설치본 — 묶을 때의 RADAR_* 환경을 설치본에 적어 둔다
//   npm run app:pack                           # 스텁 설치본 (dist/에만)
//
// 설치본은 레포 밖에서 뜨지만 백엔드는 레포의 것을 띄운다. 그래서 백엔드(Python) 변경은 앱을 다시 띄우면 들어가고,
// 화면 · 메인 프로세스(desktop/src) 변경은 다시 묶어야 들어간다.
/* eslint-disable @typescript-eslint/explicit-function-return-type -- JS 스크립트라 반환 타입을 적을 수 없다 */
import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const config = require('../electron-builder.config.cjs')
const install = process.argv.includes('--install')
const win = process.platform === 'win32'

const run = (cmd, args, opts = {}) =>
  execFileSync(cmd, args, { cwd: desktop, stdio: 'inherit', shell: win, ...opts })
const read = (cmd, args, cwd = desktop) =>
  execFileSync(cmd, args, { cwd, encoding: 'utf-8' }).trim()

// 레포 = 메인 worktree — worktree에서 묶어도 .venv · .env · 형제 폴더가 있는 메인을 띄운다
function mainRepo() {
  if (process.env.RADAR_PACK_REPO) return resolve(process.env.RADAR_PACK_REPO)
  const first = read('git', ['worktree', 'list', '--porcelain'])
    .split(/\r?\n/)
    .find((l) => l.startsWith('worktree '))
  return first ? resolve(first.slice('worktree '.length).trim()) : resolve(desktop, '..')
}

const repo = mainRepo()
if (!existsSync(join(repo, 'desktop', 'backend', 'server.py'))) {
  console.error(`[pack] 레포가 아니다: ${repo} (RADAR_PACK_REPO로 지정)`)
  process.exit(1)
}
const env = Object.fromEntries(
  Object.entries(process.env).filter(
    ([k]) => k.startsWith('RADAR_') && !k.startsWith('RADAR_PACK_')
  )
)

run(win ? 'npm.cmd' : 'npm', ['run', 'build'])

const launch = {
  repo,
  env,
  built: new Date().toISOString(),
  commit: read('git', ['rev-parse', '--short', 'HEAD'])
}
writeFileSync(join(desktop, 'out', 'launch.json'), JSON.stringify(launch, null, 2) + '\n')

run(
  process.execPath,
  [require.resolve('electron-builder/cli.js'), '--dir', '--config', 'electron-builder.config.cjs'],
  {
    shell: false
  }
)

const dist = join(desktop, 'dist')
let built
if (process.platform === 'darwin') {
  const dir = readdirSync(dist).find(
    (d) => d.startsWith('mac') && existsSync(join(dist, d, `${config.productName}.app`))
  )
  if (!dir) throw new Error('[pack] .app을 찾지 못했다')
  built = join(dist, dir, `${config.productName}.app`)
  // 알림(UNNotification)은 서명된 앱에서만 뜬다 — ad-hoc 서명 (postinstall.mjs와 같은 이유)
  run('codesign', ['--force', '--deep', '--sign', '-', built])
} else {
  built = join(dist, win ? 'win-unpacked' : 'linux-unpacked')
}

console.log(`\n[pack] 설치본: ${built}`)
console.log(`[pack] 레포: ${repo} · 커밋 ${launch.commit}`)
console.log(
  `[pack] 환경: ${
    Object.keys(env).length
      ? Object.entries(env)
          .map(([k, v]) => `${k}=${v}`)
          .join(' ')
      : '(없음 — 스텁 모드)'
  }`
)

if (install) {
  if (process.platform !== 'darwin') {
    console.log(
      '[pack] --install은 macOS만 — 위 폴더의 실행 파일을 바로 띄우거나 작업 표시줄에 고정한다'
    )
    process.exit(0)
  }
  const dest = join('/Applications', `${config.productName}.app`)
  if (existsSync(dest)) {
    const id = read('plutil', [
      '-extract',
      'CFBundleIdentifier',
      'raw',
      join(dest, 'Contents', 'Info.plist')
    ])
    if (id !== config.appId) {
      console.error(`[pack] ${dest}는 다른 앱이다 (${id}) — 손대지 않는다`)
      process.exit(1)
    }
    let running = ''
    try {
      running = read('pgrep', ['-f', `${dest}/Contents/MacOS/`])
    } catch {
      // pgrep: 없으면 1로 끝난다
    }
    if (running) {
      console.error(`[pack] ${config.productName}가 떠 있다 — 트레이에서 종료한 뒤 다시 한다`)
      process.exit(1)
    }
    rmSync(dest, { recursive: true, force: true })
  }
  run('ditto', [built, dest])
  console.log(`[pack] 설치: ${dest}`)
}

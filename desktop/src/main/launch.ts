// 설치본 실행 환경 (HYO-251) — index.ts가 가장 먼저 import한다. 다른 모듈이 process.env · 경로를 읽기 전에 돈다.
//
// 설치본(`npm run app:pack`)은 레포 밖(/Applications)에서 뜨고, Finder · Dock에서 띄우면 터미널 환경도 없다.
//   1. 레포 위치와 묶을 때의 RADAR_* 환경(예: RADAR_APP_MODE=real)은 묶을 때 resources/launch.json에 적어 두고 여기서 읽는다
//   2. macOS는 로그인 셸 환경을 받아 온다 — PATH가 /usr/bin:/bin 정도라 gh · node · claude를 못 찾는다
// 설정 · 로그 폴더는 개발 모드와 같은 자리로 고정한다 — 한 번만 뜨기 잠금도 userData를 따르므로,
// 개발 모드와 설치본이 함께 떠서 정기 실행이 두 번 도는 일이 없다.
import { app } from 'electron'
import { spawnSync } from 'child_process'
import { existsSync, readFileSync } from 'fs'
import { homedir } from 'os'
import { delimiter, join } from 'path'

export const APP_DIR = 'decision-radar-desktop' // 개발 모드의 앱 이름(package.json name) — 폴더 이름으로만 쓴다

type Launch = { repo: string; env: Record<string, string>; built?: string; commit?: string }

function readLaunch(): Launch | null {
  if (!app.isPackaged) return null
  try {
    const raw = JSON.parse(readFileSync(join(process.resourcesPath, 'launch.json'), 'utf-8'))
    if (typeof raw?.repo !== 'string') return null
    return { repo: raw.repo, env: raw.env ?? {}, built: raw.built, commit: raw.commit }
  } catch {
    return null
  }
}

const SKIP_ENV = new Set(['_', 'PWD', 'OLDPWD', 'SHLVL'])
const MARK = '__RADAR_ENV__'

function loadLoginShellEnv(): void {
  if (process.platform === 'win32') return
  const shell = process.env.SHELL || '/bin/zsh'
  const r = spawnSync(shell, ['-ilc', `printf '${MARK}'; env -0; printf '${MARK}'`], {
    encoding: 'utf-8',
    timeout: 10_000
  })
  const out = r.stdout ?? ''
  const a = out.indexOf(MARK)
  const b = out.lastIndexOf(MARK)
  if (r.status === 0 && a >= 0 && b > a) {
    for (const pair of out.slice(a + MARK.length, b).split('\0')) {
      const i = pair.indexOf('=')
      if (i <= 0) continue
      const k = pair.slice(0, i)
      if (SKIP_ENV.has(k)) continue
      if (k === 'PATH' || process.env[k] === undefined) process.env[k] = pair.slice(i + 1)
    }
    return
  }
  // 셸을 못 읽었다 — 흔한 자리만 덧붙인다
  const extra = ['/opt/homebrew/bin', '/usr/local/bin', join(homedir(), '.local', 'bin')]
  const cur = (process.env.PATH ?? '').split(delimiter).filter(Boolean)
  process.env.PATH = [...cur, ...extra.filter((p) => !cur.includes(p) && existsSync(p))].join(
    delimiter
  )
}

const launch = readLaunch()

if (app.isPackaged) {
  loadLoginShellEnv()
  for (const [k, v] of Object.entries(launch?.env ?? {})) process.env[k] = v // 묶을 때 고른 환경이 이긴다
}

app.setPath('userData', join(app.getPath('appData'), APP_DIR))
app.setAppLogsPath(
  process.platform === 'darwin'
    ? join(homedir(), 'Library', 'Logs', APP_DIR)
    : join(app.getPath('userData'), 'logs')
)

/** 설치본이 띄울 레포 — 개발 모드면 null (desktop/ 폴더의 부모를 쓴다) */
export function launchRepo(): string | null {
  return launch?.repo ?? null
}

/** 설치본인데 launch.json이 없거나 깨졌다 */
export function launchBroken(): boolean {
  return app.isPackaged && launch === null
}

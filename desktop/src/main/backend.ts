import { app } from 'electron'
import { ChildProcess, execFileSync, spawn, spawnSync } from 'child_process'
import { createWriteStream, existsSync, mkdirSync, type WriteStream } from 'fs'
import { createServer } from 'net'
import { dirname, join, resolve } from 'path'
import type { BackendStatus } from '../shared'
import { COPY } from '../copy'

const MAX_LOG_LINES = 50
const HEALTH_TIMEOUT_MS = 30_000
const HEALTH_INTERVAL_MS = 300

let state: BackendStatus = { status: 'starting' }
let child: ChildProcess | null = null
let stopping = false
const logs: string[] = []
const listeners = new Set<(s: BackendStatus) => void>()

export function repoRoot(): string {
  // dev: app.getAppPath() is the desktop/ folder; its parent is the repo root
  return resolve(dirname(app.getAppPath()))
}

export function getStatus(): BackendStatus {
  return { ...state, logs: [...logs] }
}

export function onStatus(cb: (s: BackendStatus) => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function setState(next: BackendStatus): void {
  state = next
  const snap = getStatus()
  listeners.forEach((cb) => cb(snap))
}

// 백엔드 출력을 날짜별 파일에도 남긴다 — 줄마다 시각을 붙여서 (HYO-225). 전에는 띄울 때 터미널 출력을 돌려 둔 파일뿐이라
// 띄우는 방법에 따라 로그가 없었고, 시각이 없어 언제 막혔는지 알 수 없었다. 위치: app.getPath('logs')/backend-<real|dev>-YYYY-MM-DD.log
// (실사용과 스텁 · 데모가 한 파일에 섞이지 않게 모드를 이름에)
let logFile: { day: string; stream: WriteStream } | null = null
const LOG_MODE = process.env.RADAR_APP_MODE === 'real' ? 'real' : 'dev'

function fileLog(line: string): void {
  try {
    const stamp = new Date().toLocaleString('sv-SE') // 로컬 시각 「2026-11-21 21:05:03」
    const day = stamp.slice(0, 10)
    if (!logFile || logFile.day !== day) {
      logFile?.stream.end()
      const dir = app.getPath('logs')
      mkdirSync(dir, { recursive: true })
      const path = join(dir, `backend-${LOG_MODE}-${day}.log`)
      const stream = createWriteStream(path, { flags: 'a' })
      stream.on('error', () => {
        logFile = null // 다음 줄에서 다시 연다 — 로그 파일 실패로 앱이 멈추지 않는다
      })
      logFile = { day, stream }
      console.log(`[backend] 로그 파일: ${path}`)
    }
    logFile.stream.write(`${stamp} ${line}\n`)
  } catch {
    // 로그 파일을 못 써도 앱은 돈다 — 화면 로그(logs)와 터미널 출력은 그대로
  }
}

function pushLog(chunk: string): void {
  for (const line of chunk.split(/\r?\n/)) {
    if (!line.trim()) continue
    console.log(`[backend] ${line}`)
    fileLog(line)
    logs.push(line)
    if (logs.length > MAX_LOG_LINES) logs.shift()
  }
}

function freePort(): Promise<number> {
  return new Promise((res, rej) => {
    const srv = createServer()
    srv.once('error', rej)
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      srv.close(() => res(port))
    })
  })
}

// 개발 데이터 프로필 — RADAR_DEVDATA=<이름> (비우면 default). 프로필마다 데이터 폴더가 따로다 (desktop/backend/devprofiles.py)
export function devProfile(): string {
  const name = (process.env.RADAR_DEVDATA ?? '').trim().toLowerCase() || 'default'
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) throw new Error(COPY.backend.badDevData(name))
  return name
}

function venvPython(root: string): string {
  return process.platform === 'win32'
    ? join(root, '.venv', 'Scripts', 'python.exe')
    : join(root, '.venv', 'bin', 'python')
}

function mainWorktree(root: string): string | null {
  try {
    const out = execFileSync('git', ['worktree', 'list', '--porcelain'], {
      cwd: root,
      encoding: 'utf-8'
    })
    const first = out.split(/\r?\n/).find((l) => l.startsWith('worktree '))
    return first ? first.slice('worktree '.length).trim() : null
  } catch {
    return null
  }
}

export function resolvePython(root: string): string {
  const env = process.env.RADAR_PYTHON
  if (env) return env
  const local = venvPython(root)
  if (existsSync(local)) return local
  const main = mainWorktree(root)
  if (main) {
    const alt = venvPython(main)
    if (existsSync(alt)) return alt
  }
  throw new Error(COPY.backend.noPython(local))
}

async function waitHealthy(baseUrl: string): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (stopping) return
    if (child && child.exitCode !== null) throw new Error(COPY.backend.exitedEarly)
    try {
      const res = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(1500) })
      if (res.ok) return
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, HEALTH_INTERVAL_MS))
  }
  throw new Error(COPY.backend.timeout)
}

export async function startBackend(): Promise<void> {
  setState({ status: 'starting' })
  try {
    let baseUrl = process.env.RADAR_BACKEND_URL
    if (baseUrl) {
      baseUrl = baseUrl.replace(/\/+$/, '')
    } else {
      const root = repoRoot()
      const python = resolvePython(root)
      const port = await freePort()
      const profile = devProfile()
      const dataDir =
        process.env.RADAR_APP_DATA ||
        join(root, 'desktop', profile === 'default' ? '.dev-data' : `.dev-data-${profile}`)
      const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: 'utf-8' }
      for (const k of Object.keys(env)) {
        if (/^(ANTHROPIC_API_KEY|OPENAI_API_KEY|RADAR_EXPORT)$/i.test(k)) delete env[k]
      }
      pushLog(`spawn ${python} desktop/backend/server.py --port ${port}`)
      child = spawn(
        python,
        [
          join('desktop', 'backend', 'server.py'),
          '--port',
          String(port),
          '--data-dir',
          dataDir,
          '--profile',
          profile
        ],
        { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] }
      )
      child.stdout?.on('data', (d) => pushLog(String(d)))
      child.stderr?.on('data', (d) => pushLog(String(d)))
      child.on('error', (e) => pushLog(`spawn error: ${e.message}`))
      child.on('exit', (code, sig) => {
        pushLog(`exited code=${code} signal=${sig}`)
        if (!stopping && state.status !== 'failed') {
          setState({ status: 'failed', error: COPY.backend.exited })
        }
      })
      baseUrl = `http://127.0.0.1:${port}`
    }
    await waitHealthy(baseUrl)
    if (stopping) return
    setState({ status: 'ready', baseUrl })
  } catch (e) {
    setState({ status: 'failed', error: e instanceof Error ? e.message : String(e) })
  }
}

export function stopBackend(): void {
  stopping = true
  const c = child
  child = null
  if (!c || c.pid === undefined || c.exitCode !== null) return
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/pid', String(c.pid), '/T', '/F'], { windowsHide: true })
    } else {
      c.kill('SIGTERM')
    }
  } catch {
    try {
      c.kill()
    } catch {
      // already gone
    }
  }
}

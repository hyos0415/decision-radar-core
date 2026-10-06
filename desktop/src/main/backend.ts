import { app } from 'electron'
import { ChildProcess, execFileSync, spawn, spawnSync } from 'child_process'
import { existsSync } from 'fs'
import { createServer } from 'net'
import { dirname, join, resolve } from 'path'
import type { BackendStatus } from '../shared'

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

function pushLog(chunk: string): void {
  for (const line of chunk.split(/\r?\n/)) {
    if (!line.trim()) continue
    console.log(`[backend] ${line}`)
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
  throw new Error(
    `Python을 찾지 못했다. RADAR_PYTHON을 지정하거나 ${local} 를 만든다 (메인 worktree도 확인함).`
  )
}

async function waitHealthy(baseUrl: string): Promise<void> {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS
  while (Date.now() < deadline) {
    if (stopping) return
    if (child && child.exitCode !== null) throw new Error('백엔드가 먼저 종료됐다.')
    try {
      const res = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(1500) })
      if (res.ok) return
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, HEALTH_INTERVAL_MS))
  }
  throw new Error('30초 안에 백엔드가 응답하지 않았다.')
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
      const dataDir = process.env.RADAR_APP_DATA || join(root, 'desktop', '.dev-data')
      const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: 'utf-8' }
      for (const k of Object.keys(env)) {
        if (/^(ANTHROPIC_API_KEY|OPENAI_API_KEY|RADAR_EXPORT)$/i.test(k)) delete env[k]
      }
      pushLog(`spawn ${python} desktop/backend/server.py --port ${port}`)
      child = spawn(
        python,
        [join('desktop', 'backend', 'server.py'), '--port', String(port), '--data-dir', dataDir],
        { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] }
      )
      child.stdout?.on('data', (d) => pushLog(String(d)))
      child.stderr?.on('data', (d) => pushLog(String(d)))
      child.on('error', (e) => pushLog(`spawn error: ${e.message}`))
      child.on('exit', (code, sig) => {
        pushLog(`exited code=${code} signal=${sig}`)
        if (!stopping && state.status !== 'failed') {
          setState({ status: 'failed', error: '백엔드가 종료됐다.' })
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

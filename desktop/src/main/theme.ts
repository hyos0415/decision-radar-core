import { app, nativeTheme } from 'electron'
import { readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { THEME_SOURCES, type ThemeSource } from '../shared'

// The choice lives in userData/theme.json. nativeTheme.themeSource flips both the renderer's
// prefers-color-scheme (main.css tokens) and nativeTheme.shouldUseDarkColors (window colors) (HYO-104).
function file(): string {
  return join(app.getPath('userData'), 'theme.json')
}

function isThemeSource(v: unknown): v is ThemeSource {
  return typeof v === 'string' && (THEME_SOURCES as readonly string[]).includes(v)
}

// Call before the first window is created so the first paint already uses the chosen theme.
export function loadTheme(): void {
  try {
    const v = JSON.parse(readFileSync(file(), 'utf8'))?.source
    if (isThemeSource(v)) nativeTheme.themeSource = v
  } catch {
    // no file yet or unreadable — stay on 'system'
  }
}

export function getTheme(): ThemeSource {
  return nativeTheme.themeSource
}

export function setTheme(v: unknown): ThemeSource {
  if (!isThemeSource(v)) return getTheme()
  nativeTheme.themeSource = v
  try {
    writeFileSync(file(), JSON.stringify({ source: v }))
  } catch (e) {
    console.error('[theme] save failed', e)
  }
  return v
}

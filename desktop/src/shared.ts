export type BackendStatus = {
  status: 'starting' | 'ready' | 'failed'
  baseUrl?: string
  error?: string
  logs?: string[]
}

// Where the main window should go (sent main -> renderer, or requested by the capture window).
export type NavTarget = {
  view: 'inbox' | 'decisions' | 'runs'
  id?: string
}

// App theme (HYO-104) — 'system' follows the OS. Kept by main in userData/theme.json.
export const THEME_SOURCES = ['system', 'light', 'dark'] as const
export type ThemeSource = (typeof THEME_SOURCES)[number]

export type RadarInfo = {
  shortcut: string // accelerator string, e.g. CommandOrControl+Shift+Space
  shortcutOk: boolean
  platform: string // process.platform
}

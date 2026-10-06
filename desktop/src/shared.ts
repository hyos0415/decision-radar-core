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

export type RadarInfo = {
  shortcut: string // accelerator string, e.g. CommandOrControl+Shift+Space
  shortcutOk: boolean
  platform: string // process.platform
}

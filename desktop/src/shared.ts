export type BackendStatus = {
  status: 'starting' | 'ready' | 'failed'
  baseUrl?: string
  error?: string
  logs?: string[]
}

// 상태 아이콘의 키 (HYO-101) — ADR status · Inbox 상태 · 재검토 결과 · 실행 결과. 그리는 쪽은 components/Status.tsx
export type StatusKey =
  // ADR status
  | 'proposed'
  | 'adopted'
  | 'hold'
  | 'rejected'
  | 'revisit'
  | 'superseded'
  // Inbox 상태
  | 'awaiting'
  | 'stale'
  | 'blocked'
  | 'review'
  // 재검토 조건 결과
  | 'met'
  | 'not_met'
  | 'undetermined'
  | 'unchecked'
  // 실행
  | 'running'
  | 'done'
  | 'failed'

const ADR_KEYS: readonly string[] = [
  'proposed',
  'adopted',
  'hold',
  'rejected',
  'revisit',
  'superseded'
]

export function adrStatusKey(s: string | null | undefined): StatusKey {
  if (s === 'accepted') return 'adopted' // 스킬 schema.md 이전 값
  return s && ADR_KEYS.includes(s) ? (s as StatusKey) : 'proposed'
}

export function resultKey(r: string | null | undefined): StatusKey {
  return r === 'met' || r === 'not_met' || r === 'undetermined' ? r : 'unchecked'
}

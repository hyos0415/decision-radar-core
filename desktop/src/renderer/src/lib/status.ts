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

const ADR_STATUS: Record<string, StatusKey> = {
  proposed: 'proposed',
  adopted: 'adopted',
  accepted: 'adopted',
  hold: 'hold',
  rejected: 'rejected',
  revisit: 'revisit',
  superseded: 'superseded'
}

export function adrStatusKey(s: string | null | undefined): StatusKey {
  return ADR_STATUS[s ?? ''] ?? 'proposed'
}

const RESULT: Record<string, StatusKey> = {
  met: 'met',
  not_met: 'not_met',
  undetermined: 'undetermined'
}

export function resultKey(r: string | null | undefined): StatusKey {
  return RESULT[r ?? ''] ?? 'unchecked'
}

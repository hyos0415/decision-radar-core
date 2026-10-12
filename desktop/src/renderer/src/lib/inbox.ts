import type { InboxItem, ItemState } from './api'
import type { StatusKey } from './status'

export const STATE_LABEL: Partial<Record<ItemState, string>> = {
  stale: '바뀜',
  blocked: '막힘',
  review: '검토'
}

// 목록 행의 상태 아이콘. 표시 글자가 없는 상태(승인 대기 등)도 아이콘은 있다.
const STATE_ICON: Record<ItemState, StatusKey> = {
  awaiting_approval: 'awaiting',
  stale: 'stale',
  review: 'review',
  pending: 'awaiting',
  blocked: 'blocked',
  queued: 'awaiting',
  running: 'running',
  error: 'failed',
  linked: 'done',
  no_draft: 'not_met',
  met: 'met',
  not_met: 'not_met',
  undetermined: 'undetermined'
}

export function stateIcon(state: ItemState): StatusKey {
  return STATE_ICON[state]
}

export function waitText(days: number): string {
  return days <= 0 ? '오늘 도착' : `${days}일 대기`
}

export function firstAffected(item: InboxItem): string {
  const a = item.affected[0]
  return a ? `${a.repo}/${a.component}` : ''
}

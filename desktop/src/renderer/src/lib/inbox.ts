import type { InboxItem, ItemState } from './api'
import type { StatusKey } from './status'
import { COPY } from '../../../copy'

export const STATE_LABEL: Partial<Record<ItemState, string>> = COPY.inboxState

// 목록 행의 상태 아이콘. 표시 글자가 없는 상태(승인 대기 등)도 아이콘은 있다.
export const STATE_ICON: Record<ItemState, StatusKey> = {
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
  undetermined: 'undetermined',
  check: 'undetermined'
}

export function waitText(days: number): string {
  return days <= 0 ? COPY.inbox.waitToday : COPY.inbox.waitDays(days)
}

export function firstAffected(item: InboxItem): string {
  const a = item.affected[0]
  return a ? `${a.repo}/${a.component}` : ''
}

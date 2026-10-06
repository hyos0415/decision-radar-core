import type { InboxItem, ItemState } from './api'

export const STATE_LABEL: Partial<Record<ItemState, string>> = {
  stale: '바뀜',
  blocked: '막힘',
  review: '검토'
}

export function waitText(days: number): string {
  return days <= 0 ? '오늘 도착' : `${days}일 대기`
}

export function firstAffected(item: InboxItem): string {
  const a = item.affected[0]
  return a ? `${a.repo}/${a.component}` : ''
}

import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  Check,
  Circle,
  Loader2,
  RefreshCw,
  X
} from 'lucide-react'
import { cn } from '@renderer/lib/utils'

import type { StatusKey } from '@renderer/lib/status'

const TONE: Record<StatusKey, string> = {
  proposed: 'tone-info',
  adopted: 'tone-success',
  hold: 'tone-warning',
  rejected: 'tone-neutral',
  revisit: 'tone-warning',
  superseded: 'tone-neutral',
  awaiting: 'tone-foreground',
  stale: 'tone-warning',
  blocked: 'tone-danger',
  review: 'tone-info',
  met: 'tone-foreground',
  not_met: 'tone-neutral',
  undetermined: 'tone-warning',
  unchecked: 'tone-faint',
  running: 'tone-neutral',
  done: 'tone-success',
  failed: 'tone-danger'
}

export function StatusIcon({ status }: { status: StatusKey }): React.JSX.Element {
  const tone = TONE[status]
  switch (status) {
    case 'adopted':
    case 'done':
      return (
        <span className={cn('status-icon filled', tone)}>
          <Check size={9} strokeWidth={3} />
        </span>
      )
    case 'met':
      return <span className={cn('status-icon filled', tone)} />
    case 'running':
      return <Loader2 className={cn('status-svg spin', tone)} size={14} />
    case 'stale':
      return <AlertTriangle className={cn('status-svg', tone)} size={14} />
    case 'blocked':
      return <AlertCircle className={cn('status-svg', tone)} size={14} />
    case 'review':
      return (
        <Circle
          className={cn('status-svg', tone)}
          size={14}
          fill="currentColor"
          fillOpacity=".18"
        />
      )
    case 'revisit':
      return <RefreshCw className={cn('status-svg', tone)} size={14} />
    case 'superseded':
      return <ArrowRight className={cn('status-svg circled', tone)} size={14} />
    case 'unchecked':
      return <span className={cn('status-dash', tone)}>—</span>
    case 'rejected':
    case 'failed':
      return (
        <span className={cn('status-icon', tone)}>
          <X size={9} strokeWidth={3} />
        </span>
      )
    case 'hold':
      return <span className={cn('status-icon half', tone)} />
    case 'awaiting':
      return <span className={cn('status-icon awaiting', tone)} />
    case 'proposed':
    case 'undetermined':
      return <span className={cn('status-icon dashed', tone)} />
    default:
      return <span className={cn('status-icon', tone)} />
  }
}

export function Status({
  status,
  label,
  strong = false
}: {
  status: StatusKey
  label: string
  strong?: boolean
}): React.JSX.Element {
  return (
    <span className={cn('status', strong && 'strong')}>
      <StatusIcon status={status} />
      <span>{label}</span>
    </span>
  )
}

export function PageHeader({
  title,
  count,
  subtitle,
  children
}: {
  title: string
  count?: number
  subtitle?: string
  children?: React.ReactNode
}): React.JSX.Element {
  return (
    <header className="page-header">
      <div className="page-title">
        <h1>{title}</h1>
        {count !== undefined && <span className="header-count">{count}</span>}
        {subtitle && <span className="page-subtitle">{subtitle}</span>}
      </div>
      {children}
    </header>
  )
}

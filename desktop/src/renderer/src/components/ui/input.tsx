import * as React from 'react'
import { cn } from '@renderer/lib/utils'

const field =
  'w-full min-w-0 rounded-md border bg-background px-2.5 text-[13px] outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:opacity-50'

export function Input({ className, ...props }: React.ComponentProps<'input'>): React.JSX.Element {
  return <input className={cn(field, 'h-8', className)} {...props} />
}

export function Textarea({
  className,
  ...props
}: React.ComponentProps<'textarea'>): React.JSX.Element {
  return <textarea className={cn(field, 'min-h-16 py-1.5', className)} {...props} />
}

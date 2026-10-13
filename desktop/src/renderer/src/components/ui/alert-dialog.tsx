import * as React from 'react'
import * as AlertPrimitive from '@radix-ui/react-alert-dialog'
import { cn } from '@renderer/lib/utils'
import { Button } from './button'

export const AlertDialog = AlertPrimitive.Root
export const AlertDialogTrigger = AlertPrimitive.Trigger

export function AlertDialogContent({
  className,
  ...props
}: React.ComponentProps<typeof AlertPrimitive.Content>): React.JSX.Element {
  return (
    <AlertPrimitive.Portal>
      <AlertPrimitive.Overlay className="fixed inset-0 z-50 bg-black/30" />
      <AlertPrimitive.Content
        className={cn(
          'fixed top-1/2 left-1/2 z-50 grid w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 gap-3 rounded-lg border bg-background p-5 shadow-lg outline-none',
          className
        )}
        {...props}
      />
    </AlertPrimitive.Portal>
  )
}

export function AlertDialogTitle(
  props: React.ComponentProps<typeof AlertPrimitive.Title>
): React.JSX.Element {
  return <AlertPrimitive.Title className="text-sm font-semibold" {...props} />
}

export function AlertDialogDescription(
  props: React.ComponentProps<typeof AlertPrimitive.Description>
): React.JSX.Element {
  return <AlertPrimitive.Description className="text-xs text-muted-foreground" {...props} />
}

export function AlertDialogAction(
  props: React.ComponentProps<typeof AlertPrimitive.Action>
): React.JSX.Element {
  return (
    <AlertPrimitive.Action asChild>
      <Button {...props} />
    </AlertPrimitive.Action>
  )
}

export function AlertDialogCancel(
  props: React.ComponentProps<typeof AlertPrimitive.Cancel>
): React.JSX.Element {
  return (
    <AlertPrimitive.Cancel asChild>
      <Button variant="outline" {...props} />
    </AlertPrimitive.Cancel>
  )
}

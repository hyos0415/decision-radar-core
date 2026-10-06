import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@renderer/lib/utils'

const badgeVariants = cva(
  'inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[11px] leading-none font-medium whitespace-nowrap',
  {
    variants: {
      variant: {
        outline: 'border text-foreground',
        secondary: 'bg-secondary text-secondary-foreground',
        default: 'bg-primary text-primary-foreground'
      }
    },
    defaultVariants: { variant: 'outline' }
  }
)

export function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<'span'> & VariantProps<typeof badgeVariants>): React.JSX.Element {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

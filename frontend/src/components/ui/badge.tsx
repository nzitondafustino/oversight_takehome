import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "border-status-neutral-border bg-status-neutral-soft text-muted-foreground",
        pass: "border-status-pass-border bg-status-pass-soft text-status-pass",
        review: "border-status-review-border bg-status-review-soft text-status-review-text",
        flag: "border-status-flag-border bg-status-flag-soft text-status-flag-text",
        info: "border-status-info-border bg-status-info-soft text-primary",
        accent: "border-transparent bg-accent-soft text-accent-foreground",
        outline: "border-border bg-card text-muted-foreground",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };

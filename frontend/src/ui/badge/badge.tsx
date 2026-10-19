import * as React from "react";
import { Check, Circle, Loader2, Pause, X } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
const badgeVariants = cva("ui-badge", {
  variants: {
    variant: {
      ACTIVE: "ui-badge-ACTIVE",
      OFFLINE: "ui-badge-OFFLINE",
      WARNING: "ui-badge-WARNING",
      CRITICAL: "ui-badge-CRITICAL",
      SCANNING: "ui-badge-SCANNING",
    },
  },
  defaultVariants: { variant: "ACTIVE" },
});
export interface BadgeProps
  extends
    React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}
function Badge({
  className,
  variant = "ACTIVE",
  children,
  ...props
}: BadgeProps) {
  const Icon =
    variant === "ACTIVE"
      ? Check
      : variant === "CRITICAL"
        ? X
        : variant === "WARNING"
          ? Pause
          : variant === "SCANNING"
            ? Loader2
            : Circle;
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props}>
      <Icon size={11} aria-hidden="true" />
      {children}
    </span>
  );
}
export { Badge, badgeVariants };

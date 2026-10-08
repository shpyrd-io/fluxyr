// Adapted from https://www.scificn.dev/r/badge.json (2026-10-07).
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  [
    "inline-flex items-center gap-1.5",
    "px-2 py-0.5",
    "text-[0.65rem] font-mono font-medium uppercase tracking-widest",
    "border border-solid",
    "rounded-none",
    "whitespace-nowrap",
  ],
  {
    variants: {
      variant: {
        ACTIVE: ["border-[var(--color-green)]", "text-[var(--color-green)]"],
        OFFLINE: ["border-[var(--border)]", "text-[var(--text-muted)]"],
        WARNING: ["border-[var(--color-amber)]", "text-[var(--color-amber)]"],
        CRITICAL: ["border-[var(--color-red)]", "text-[var(--color-red)]"],
        SCANNING: ["border-[var(--color-green)]", "text-[var(--color-green)]"],
      },
    },
    defaultVariants: {
      variant: "ACTIVE",
    },
  },
);

export interface BadgeProps
  extends
    React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, children, ...props }: BadgeProps) {
  const isScanning = variant === "SCANNING";

  return (
    <span
      className={cn(badgeVariants({ variant }), className)}
      style={
        variant === "ACTIVE"
          ? {
              textShadow: "var(--text-glow-green)",
              boxShadow:
                "inset 0 0 8px color-mix(in srgb, var(--color-green) 7%, transparent)",
            }
          : variant === "CRITICAL"
            ? {
                textShadow: "var(--text-glow-red)",
                boxShadow:
                  "inset 0 0 8px color-mix(in srgb, var(--color-red) 7%, transparent)",
              }
            : undefined
      }
      {...props}
    >
      {isScanning && (
        <span
          style={{
            display: "inline-block",
            animation: "blink-cursor 1s step-end infinite",
          }}
        >
          ●
        </span>
      )}
      {!isScanning && variant === "ACTIVE" && <span>●</span>}
      {!isScanning && variant === "OFFLINE" && <span>○</span>}
      {!isScanning && variant === "WARNING" && <span>⚠</span>}
      {!isScanning && variant === "CRITICAL" && <span>✕</span>}
      {children}
    </span>
  );
}

export { Badge, badgeVariants };

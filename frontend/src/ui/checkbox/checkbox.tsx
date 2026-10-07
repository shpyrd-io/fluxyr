// Adapted from https://www.scificn.dev/r/checkbox.json (2026-10-07).
import * as React from "react";
import { Label } from "../label/label";
import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import { cn } from "@/lib/utils";

export interface CheckboxProps extends React.ComponentPropsWithoutRef<
  typeof CheckboxPrimitive.Root
> {
  label?: string;
}

const Checkbox = React.forwardRef<
  React.ElementRef<typeof CheckboxPrimitive.Root>,
  CheckboxProps
>(({ className, label, id, ...props }, ref) => {
  const generatedId = React.useId();
  const checkboxId = id ?? generatedId;

  return (
    <div
      style={{ display: "inline-flex", alignItems: "center", gap: "0.5rem" }}
    >
      <CheckboxPrimitive.Root
        ref={ref}
        id={checkboxId}
        className={cn(
          "peer h-4 w-4 shrink-0 rounded-none",
          "border border-[var(--border)]",
          "bg-[var(--surface)]",
          "focus-visible:outline-none",
          "focus-visible:border-[var(--border-active)]",
          "focus-visible:shadow-[var(--glow-green)]",
          "disabled:cursor-not-allowed disabled:opacity-40",
          "data-[state=checked]:border-[var(--color-green)]",
          "data-[state=checked]:bg-[var(--surface-raised)]",
          "data-[state=checked]:shadow-[var(--glow-green)]",
          "transition-all duration-150",
          className,
        )}
        {...props}
      >
        <CheckboxPrimitive.Indicator
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: "100%",
            height: "100%",
          }}
        >
          <span
            style={{
              color: "var(--color-green)",
              fontSize: "0.6rem",
              lineHeight: 1,
              textShadow: "var(--text-glow-green)",
              userSelect: "none",
            }}
          >
            ■
          </span>
        </CheckboxPrimitive.Indicator>
      </CheckboxPrimitive.Root>

      {label && (
        <Label
          htmlFor={checkboxId}
          style={{
            fontSize: "0.75rem",
            color: "var(--text-secondary)",
            letterSpacing: "0.05em",
            cursor: "pointer",
            userSelect: "none",
          }}
        >
          {label}
        </Label>
      )}
    </div>
  );
});
Checkbox.displayName = CheckboxPrimitive.Root.displayName;

export { Checkbox };

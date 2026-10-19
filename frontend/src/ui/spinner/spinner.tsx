import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
export interface SpinnerProps extends React.HTMLAttributes<HTMLSpanElement> {
  size?: "SM" | "MD" | "LG";
  label?: string;
}
function Spinner({ className, size = "MD", label, ...props }: SpinnerProps) {
  return (
    <span
      className={cn("ui-spinner", className)}
      role="status"
      aria-label={label ?? "Loading"}
      {...props}
    >
      <Loader2 size={{ SM: 12, MD: 14, LG: 18 }[size]} aria-hidden="true" />
      {label}
    </span>
  );
}
export { Spinner };

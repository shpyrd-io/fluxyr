import React from "react";
import { Button as ScificnButton, type ButtonProps } from "./ui/button/button";
import symbol from "../../assets/symbol.svg";
import wordmark from "../../assets/logo-text.svg";
export { Badge } from "./ui/badge/badge";
export { Panel, PanelHeader, PanelTitle, PanelContent } from "./ui/panel/panel";
export { Input } from "./ui/input/input";
export { Textarea } from "./ui/textarea/textarea";
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant, type = "button", ...props }, ref) => (
    <ScificnButton
      ref={ref}
      type={type}
      variant={
        variant ||
        (className.includes("primary")
          ? "EXEC"
          : className.includes("danger")
            ? "ABORT"
            : "OUTLINE")
      }
      className={`fluxyr-button ${className}`}
      {...props}
    />
  ),
);
Button.displayName = "Button";
export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`fluxyr-symbol ${className}`} aria-hidden="true">
      <img src={symbol} alt="" />
    </span>
  );
}
export function Wordmark() {
  return <img className="wordmark" src={wordmark} alt="Fluxyr" />;
}
export function DebugId({ id, label = "job" }: { id: string; label?: string }) {
  if (!id) return null;
  return (
    <code className="debug-id" title={`${label}: ${id}`}>
      {label} {id.length > 12 ? id.slice(0, 8) : id}
    </code>
  );
}

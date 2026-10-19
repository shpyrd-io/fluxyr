import * as React from "react";
import { cn } from "@/lib/utils";
export interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Retained for compatibility; panels now use the shared radius. */ notch?:
    "sm" | "md" | "lg" | "none";
}
const Panel = React.forwardRef<HTMLDivElement, PanelProps>(
  ({ className, notch: _notch, ...props }, ref) => (
    <div ref={ref} className={cn("ui-panel", className)} {...props} />
  ),
);
const PanelHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("ui-panel-header", className)} {...props} />
));
const PanelTitle = React.forwardRef<
  HTMLHeadingElement,
  React.HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3 ref={ref} className={cn("ui-panel-title", className)} {...props} />
));
const PanelContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("ui-panel-content", className)} {...props} />
));
const PanelFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn("ui-panel-footer", className)} {...props} />
));
Panel.displayName = "Panel";
PanelHeader.displayName = "PanelHeader";
PanelTitle.displayName = "PanelTitle";
PanelContent.displayName = "PanelContent";
PanelFooter.displayName = "PanelFooter";
export { Panel, PanelHeader, PanelTitle, PanelContent, PanelFooter };

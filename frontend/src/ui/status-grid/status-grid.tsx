import * as React from "react";
import { cn } from "@/lib/utils";
export type SystemStatus =
  "ACTIVE" | "OFFLINE" | "WARNING" | "CRITICAL" | "SCANNING";
export interface SystemEntry {
  name: string;
  status: SystemStatus;
  detail?: string;
}
export interface StatusGridProps extends React.HTMLAttributes<HTMLDivElement> {
  systems: SystemEntry[];
  title?: string;
  columns?: 1 | 2 | 3;
}
const StatusGrid = React.forwardRef<HTMLDivElement, StatusGridProps>(
  ({ className, systems, title, columns = 1, ...props }, ref) => (
    <div ref={ref} className={cn("ui-runtime", className)} {...props}>
      {title && <div className="ui-runtime-title">{title}</div>}
      <div
        className="ui-runtime-grid"
        style={{ gridTemplateColumns: `repeat(${columns},minmax(0,1fr))` }}
      >
        {systems.map((system) => (
          <div key={system.name} className="ui-runtime-row">
            <span
              className="ui-runtime-dot"
              data-status={system.status}
              role="img"
              aria-label={system.status.toLowerCase()}
              title={system.status.toLowerCase()}
            />
            <span>{system.name}</span>
            {system.detail && (
              <span className="ui-runtime-detail">{system.detail}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  ),
);
StatusGrid.displayName = "StatusGrid";
export { StatusGrid };

import type { ReactNode } from "react";
import { Search, type LucideIcon } from "lucide-react";
import { Input } from "./components";

/** Shared list anatomy for resources, executions and files. */
export function CatalogueSearch({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="catalogue-search">
      <Search size={15} aria-hidden="true" />
      <Input
        aria-label={label}
        placeholder={`${label}…`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
export function CatalogueIdentity({
  icon: Icon,
  title,
  children,
  code = false,
}: {
  icon: LucideIcon;
  title: string;
  children?: ReactNode;
  code?: boolean;
}) {
  return (
    <span className="catalogue-identity">
      <Icon size={18} aria-hidden="true" />
      <span>
        <strong className={code ? "catalogue-code-title" : undefined}>
          {title}
        </strong>
        {children && <span className="catalogue-secondary">{children}</span>}
      </span>
    </span>
  );
}
export function CatalogueEmpty({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="catalogue-empty">
      <Icon size={24} aria-hidden="true" />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}

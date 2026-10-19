import * as React from "react";
import { Label } from "../label/label";
import { cn } from "@/lib/utils";
export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  prefix?: string;
}
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, label, error, id, prefix, ...props }, ref) => {
    const generatedId = React.useId();
    const fieldId = id ?? generatedId;
    const errorId = fieldId + "-error";
    return (
      <div className="ui-field">
        {label && <Label htmlFor={fieldId}>{label}</Label>}
        <div className="ui-field-control">
          {prefix && (
            <span className="ui-field-prefix" aria-hidden="true">
              {prefix}
            </span>
          )}
          <input
            ref={ref}
            id={fieldId}
            {...props}
            aria-invalid={error ? true : props["aria-invalid"]}
            aria-describedby={
              error
                ? [props["aria-describedby"], errorId].filter(Boolean).join(" ")
                : props["aria-describedby"]
            }
            className={cn("ui-input", prefix && "ui-input-prefix", className)}
          />
        </div>
        {error && (
          <span id={errorId} className="ui-field-error">
            {error}
          </span>
        )}
      </div>
    );
  },
);
Input.displayName = "Input";
export { Input };

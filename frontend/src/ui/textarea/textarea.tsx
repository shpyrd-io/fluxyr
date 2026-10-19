import * as React from "react";
import { Label } from "../label/label";
import { cn } from "@/lib/utils";
export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}
const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, label, error, id, ...props }, ref) => {
    const generatedId = React.useId();
    const fieldId = id ?? generatedId;
    const errorId = fieldId + "-error";
    return (
      <div className="ui-field">
        {label && <Label htmlFor={fieldId}>{label}</Label>}
        <div className="ui-field-control">
          <textarea
            ref={ref}
            id={fieldId}
            {...props}
            aria-invalid={error ? true : props["aria-invalid"]}
            aria-describedby={
              error
                ? [props["aria-describedby"], errorId].filter(Boolean).join(" ")
                : props["aria-describedby"]
            }
            className={cn("ui-textarea", className)}
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
Textarea.displayName = "Textarea";
export { Textarea };

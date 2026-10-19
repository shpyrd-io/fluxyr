import * as React from "react";
import * as Primitive from "@radix-ui/react-checkbox";
import { Label } from "../label/label";
import { cn } from "@/lib/utils";
import { Check } from "lucide-react";
export interface CheckboxProps extends React.ComponentPropsWithoutRef<
  typeof Primitive.Root
> {
  label?: string;
}
const Checkbox = React.forwardRef<
  React.ElementRef<typeof Primitive.Root>,
  CheckboxProps
>(({ className, label, id, ...props }, ref) => {
  const generatedId = React.useId();
  const fieldId = id ?? generatedId;
  return (
    <div className="ui-check-field">
      <Primitive.Root
        ref={ref}
        id={fieldId}
        className={cn("ui-checkbox", className)}
        {...props}
      >
        <Primitive.Indicator>
          <Check size={12} strokeWidth={2.5} aria-hidden="true" />
        </Primitive.Indicator>
      </Primitive.Root>
      {label && <Label htmlFor={fieldId}>{label}</Label>}
    </div>
  );
});
Checkbox.displayName = "Checkbox";
export { Checkbox };

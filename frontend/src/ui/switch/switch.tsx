import * as React from "react";
import * as Primitive from "@radix-ui/react-switch";
import { Label } from "../label/label";
import { cn } from "@/lib/utils";

export interface SwitchProps extends React.ComponentPropsWithoutRef<
  typeof Primitive.Root
> {
  label?: string;
}
const Switch = React.forwardRef<
  React.ElementRef<typeof Primitive.Root>,
  SwitchProps
>(({ className, label, id, ...props }, ref) => {
  const generatedId = React.useId();
  const fieldId = id ?? generatedId;
  return (
    <div className="ui-check-field">
      <Primitive.Root
        ref={ref}
        id={fieldId}
        className={cn("ui-switch", className)}
        {...props}
      >
        <Primitive.Thumb className="ui-switch-thumb" />
      </Primitive.Root>
      {label && <Label htmlFor={fieldId}>{label}</Label>}
    </div>
  );
});
Switch.displayName = "Switch";
export { Switch };

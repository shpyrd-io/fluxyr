import * as React from "react";
import * as Primitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
const Select = Primitive.Root,
  SelectGroup = Primitive.Group,
  SelectValue = Primitive.Value;
const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof Primitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof Primitive.Trigger> & {
    showIndicator?: boolean;
  }
>(({ className, children, showIndicator = true, ...props }, ref) => (
  <Primitive.Trigger
    ref={ref}
    className={cn("ui-select-trigger", className)}
    {...props}
  >
    {children}
    {showIndicator && (
      <Primitive.Icon asChild>
        <ChevronDown size={14} />
      </Primitive.Icon>
    )}
  </Primitive.Trigger>
));
const SelectContent = React.forwardRef<
  React.ElementRef<typeof Primitive.Content>,
  React.ComponentPropsWithoutRef<typeof Primitive.Content>
>(({ className, children, position = "popper", ...props }, ref) => (
  <Primitive.Portal>
    <Primitive.Content
      ref={ref}
      className={cn(
        "ui-select-content",
        position === "popper" && "w-[var(--radix-select-trigger-width)]",
        className,
      )}
      position={position}
      sideOffset={5}
      {...props}
    >
      <Primitive.Viewport className="ui-select-viewport">
        {children}
      </Primitive.Viewport>
    </Primitive.Content>
  </Primitive.Portal>
));
const SelectLabel = React.forwardRef<
  React.ElementRef<typeof Primitive.Label>,
  React.ComponentPropsWithoutRef<typeof Primitive.Label>
>(({ className, ...props }, ref) => (
  <Primitive.Label
    ref={ref}
    className={cn("ui-select-label", className)}
    {...props}
  />
));
const SelectItem = React.forwardRef<
  React.ElementRef<typeof Primitive.Item>,
  React.ComponentPropsWithoutRef<typeof Primitive.Item>
>(({ className, children, ...props }, ref) => (
  <Primitive.Item
    ref={ref}
    className={cn("ui-select-item", className)}
    {...props}
  >
    <Primitive.ItemText>{children}</Primitive.ItemText>
    <Primitive.ItemIndicator className="ui-select-indicator">
      <Check size={13} />
    </Primitive.ItemIndicator>
  </Primitive.Item>
));
const SelectSeparator = React.forwardRef<
  React.ElementRef<typeof Primitive.Separator>,
  React.ComponentPropsWithoutRef<typeof Primitive.Separator>
>(({ className, ...props }, ref) => (
  <Primitive.Separator
    ref={ref}
    className={cn("ui-select-separator", className)}
    {...props}
  />
));
SelectTrigger.displayName = "SelectTrigger";
SelectContent.displayName = "SelectContent";
SelectLabel.displayName = "SelectLabel";
SelectItem.displayName = "SelectItem";
SelectSeparator.displayName = "SelectSeparator";
export {
  Select,
  SelectGroup,
  SelectValue,
  SelectTrigger,
  SelectContent,
  SelectLabel,
  SelectItem,
  SelectSeparator,
};

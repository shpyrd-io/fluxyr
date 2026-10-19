import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
const buttonVariants = cva("ui-button", {
  variants: {
    variant: {
      EXEC: "ui-button-primary",
      GHOST: "ui-button-ghost",
      OUTLINE: "ui-button-outline",
      ABORT: "ui-button-danger",
    },
    size: { SM: "ui-button-sm", MD: "", LG: "ui-button-lg" },
  },
  defaultVariants: { variant: "OUTLINE", size: "MD" },
});
export interface ButtonProps
  extends
    React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}
const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
export { Button, buttonVariants };

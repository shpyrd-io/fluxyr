import { Children, isValidElement, type ReactNode } from "react";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "./ui/select/select";

type OptionProps = {
  value?: string | number;
  children?: ReactNode;
  disabled?: boolean;
};
export function SelectOption(_props: OptionProps) {
  return null;
}

// A small application adapter keeps option lists declarative while the Scificn
// Radix component owns keyboard navigation, focus, portal and selection state.
export function SelectField({
  value,
  onValueChange,
  children,
  disabled,
  required,
  name,
  ...trigger
}: {
  value: string | number;
  onValueChange: (value: string) => void;
  children: ReactNode;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  id?: string;
  className?: string;
  "aria-label"?: string;
}) {
  const options: OptionProps[] = [];
  Children.forEach(children, (child) => {
    if (isValidElement<OptionProps>(child)) options.push(child.props);
  });
  return (
    <Select
      value={`option:${value ?? ""}`}
      onValueChange={(v) => onValueChange(v.slice(7))}
      disabled={disabled}
      required={required}
      name={name}
    >
      <SelectTrigger
        {...trigger}
        className={`form-select ${trigger.className || ""}`}
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option, i) => (
          <SelectItem
            key={i}
            value={`option:${option.value ?? option.children}`}
            disabled={option.disabled}
          >
            {option.children}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

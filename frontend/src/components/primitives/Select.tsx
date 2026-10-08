import * as RS from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { useId, type ReactNode } from "react";
import { cx } from "./cx";
import { fieldClass, type FieldProps } from "./Input";

export interface SelectOption { value: string; label: ReactNode; disabled?: boolean }

/** An accessible select (keyboard, screen reader) on Radix. `value` / `onChange` are plain strings. */
export function Select({ value, onChange, options, placeholder = "Select…", label, hint, error, disabled, className }: {
  value: string | undefined; onChange: (v: string) => void; options: SelectOption[]; placeholder?: string;
  disabled?: boolean; className?: string;
} & FieldProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div className="space-y-1">
      {label && <label htmlFor={id} className="block text-xs font-medium text-fg-muted">{label}</label>}
      <RS.Root value={value} onValueChange={onChange} disabled={disabled}>
        <RS.Trigger id={id} aria-invalid={error ? true : undefined} aria-describedby={describedBy} className={cx(fieldClass, "flex items-center justify-between gap-2 text-left", className)}>
          <RS.Value placeholder={<span className="text-fg-muted">{placeholder}</span>} />
          <RS.Icon><ChevronDown size={14} className="text-fg-muted" /></RS.Icon>
        </RS.Trigger>
        <RS.Portal>
          <RS.Content position="popper" sideOffset={4}
            className="z-50 max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-border bg-surface-1 shadow-card">
            <RS.Viewport className="p-1">
              {options.map((o) => (
                <RS.Item key={o.value} value={o.value} disabled={o.disabled}
                  className="relative flex cursor-pointer select-none items-center rounded px-7 py-1.5 text-sm text-fg outline-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface-2">
                  <RS.ItemIndicator className="absolute left-2"><Check size={13} /></RS.ItemIndicator>
                  <RS.ItemText>{o.label}</RS.ItemText>
                </RS.Item>
              ))}
            </RS.Viewport>
          </RS.Content>
        </RS.Portal>
      </RS.Root>
      {error ? <p id={`${id}-error`} role="alert" className="text-xs text-down">{error}</p>
        : hint ? <p id={`${id}-hint`} className="text-xs text-fg-muted">{hint}</p> : null}
    </div>
  );
}

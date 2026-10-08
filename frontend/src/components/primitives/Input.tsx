import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";
import { cx } from "./cx";

export const fieldClass = cx("h-9 w-full rounded-md border border-border bg-surface-2 px-3 text-sm text-fg",
  "placeholder:text-fg-muted/70 focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand",
  "disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-down");

export interface FieldProps { label?: ReactNode; hint?: ReactNode; error?: ReactNode }

/** A labelled text / number input. Numbers use tabular digits; an `error` is announced and marks the field invalid. */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & FieldProps>(function Input(
  { label, hint, error, className, id, type = "text", ...rest }, ref,
) {
  const auto = useId();
  const fieldId = id ?? auto;
  const describedBy = error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined;
  return (
    <div className="space-y-1">
      {label && <label htmlFor={fieldId} className="block text-xs font-medium text-fg-muted">{label}</label>}
      <input ref={ref} id={fieldId} type={type} aria-invalid={error ? true : undefined} aria-describedby={describedBy}
        className={cx(fieldClass, type === "number" && "font-tabular", className)} {...rest} />
      {error ? <p id={`${fieldId}-error`} role="alert" className="text-xs text-down">{error}</p>
        : hint ? <p id={`${fieldId}-hint`} className="text-xs text-fg-muted">{hint}</p> : null}
    </div>
  );
});

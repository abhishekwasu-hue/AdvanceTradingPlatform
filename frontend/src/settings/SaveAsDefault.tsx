import { useState } from "react";
import { request } from "../api/client";
import { saveAsDefault } from "./tradeSettings";

/**
 * M68 TS-3 plumbing: the "Save as my default" tick next to one field of a ticket or a form. Ticking it writes that one
 * field - its current value - to the trader's own Trade settings (Settings › Trade settings); nothing else is written, and
 * an empty field is never saved. Not used yet: the order ticket (PR-1) and the bot form (PR-2) place it next to a field.
 */
export function SaveAsDefault({ fieldKey, value, disabled = false, fetcher = request, onSaved }: {
  /** The Trade settings key, e.g. "order_defaults.product". */
  fieldKey: string;
  /** The field's current value in the ticket; empty = nothing to save. */
  value: unknown;
  disabled?: boolean;
  fetcher?: typeof request;
  onSaved?: (key: string, value: unknown) => void;
}) {
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const empty = value === null || value === undefined || (typeof value === "string" && value.trim() === "");
  const onTick = async (checked: boolean) => {
    if (!checked) { setState("idle"); return; }
    setState("saving");
    try {
      const done = await saveAsDefault(fieldKey, value, fetcher);
      setState(done ? "saved" : "idle");
      if (done) onSaved?.(fieldKey, value);
    } catch {
      setState("failed");
    }
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-space-2" data-save-default={fieldKey}>
      <label className="inline-flex min-h-11 items-center gap-space-2 text-t13 text-fg md:min-h-9">
        <input type="checkbox" className="h-5 w-5 accent-brand" checked={state === "saved" || state === "saving"}
          disabled={disabled || empty || state === "saving"} onChange={(e) => void onTick(e.target.checked)} />
        Save as my default
      </label>
      {state === "saved" && <span className="text-t12 text-up" data-save-default-note>Saved</span>}
      {state === "failed" && <span className="text-t12 text-down" data-save-default-note>Not saved. Try again.</span>}
    </span>
  );
}

export default SaveAsDefault;

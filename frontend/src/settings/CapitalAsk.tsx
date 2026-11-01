import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { formatNumber } from "../ui/format";
import { saveAsDefault } from "./tradeSettings";

/** M68 PR-7 (TS-3, D4): the Trade settings key of the paper starting capital. */
export const CAPITAL_KEY = "paper_account.capital";
/** The server's words when a paper order waits for the starting capital (backend app/paper/paper_settings.py). */
export const NEEDS_CAPITAL = "Needs your setting: Starting capital";

/** True when one of the order's refusals is "the starting capital is Not set". */
export const needsCapital = (reasons: readonly string[] | undefined): boolean => !!reasons?.some((r) => r.includes(NEEDS_CAPITAL));

/** The amount typed ("10,00,000" -> 1000000), or null when it is not a number above zero. */
export function capitalOf(text: string): number | null {
  const n = Number(text.trim().replace(/,/g, ""));
  return text.trim() !== "" && Number.isFinite(n) && n > 0 ? Math.round(n * 100) / 100 : null;
}

/** Where the capital he typed goes: his paper account (and, ticked, his Trade settings default too); with the paper
 * account off (404) his Trade settings value is its only home. Returns where it was saved. */
export async function saveCapital(amount: number, asDefault: boolean, io: {
  setPaperCapital: (amount: number) => Promise<unknown>; saveDefault: (key: string, value: unknown) => Promise<boolean>;
} = { setPaperCapital: (n) => api.setPaperCapital(n), saveDefault: (k, v) => saveAsDefault(k, v) }): Promise<"account" | "settings"> {
  let onAccount = true;
  try {
    await io.setPaperCapital(amount);
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) throw e;
    onAccount = false;
  }
  if (asDefault || !onAccount) await io.saveDefault(CAPITAL_KEY, amount);
  return onAccount ? "account" : "settings";
}

/**
 * M68 PR-7 (owner decision D4, default M68Q-4): ATP chooses no starting capital. When his paper account has none, the
 * first paper order ASKS for it here, the field focused, with "Save as my default" (his Trade settings value - a new
 * paper account starts with it). Empty until he types; nothing is filled in for him. The account takes the amount
 * (`PUT /paper/account`); with the paper account off, the Trade settings value is the only home, so it is saved there.
 */
export function CapitalAsk({ onSaved, autoFocus = true, id = "paper-capital" }: {
  onSaved: (amount: number) => void; autoFocus?: boolean; id?: string;
}) {
  const [text, setText] = useState("");
  const [asDefault, setAsDefault] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => { if (autoFocus) field.current?.focus(); }, [autoFocus]);
  const amount = capitalOf(text);

  async function save() {
    if (amount === null) { setProblem("Enter your starting capital: an amount above 0."); return; }
    setBusy(true); setProblem(null);
    try {
      await saveCapital(amount, asDefault);
      onSaved(amount);
    } catch (e) {
      setProblem(e instanceof ApiError && e.detail ? `Not saved: ${e.detail}` : "Not saved. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="basis-full space-y-space-2 rounded-control border border-brand bg-surface-2 px-space-3 py-space-3" data-capital-ask>
      <label htmlFor={id} className="block text-t13 font-medium text-fg">Your paper starting capital</label>
      <p className="text-t12 text-fg-muted">Your paper balance and drawdown are measured from it. ATP does not choose one for you.</p>
      <div className="flex flex-wrap items-center gap-space-2">
        <span className="inline-flex h-11 min-w-0 flex-1 items-center rounded-control border border-border bg-surface-1 px-space-3 focus-within:border-brand">
          <span className="text-t14 text-fg-muted" aria-hidden>₹</span>
          <input id={id} ref={field} inputMode="decimal" autoComplete="off" value={text} placeholder="Not set" data-capital-field
            onChange={(e) => { setText(e.target.value); setProblem(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void save(); } }}
            className="h-full min-w-0 flex-1 bg-transparent px-space-2 text-t14 text-fg font-tabular focus:outline-none" />
        </span>
        <button type="button" onClick={() => void save()} disabled={busy || amount === null} data-capital-save
          className="min-h-11 rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand disabled:opacity-60">
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
      {amount !== null && <p className="text-t12 text-fg-muted font-tabular" data-capital-shown>₹{formatNumber(amount, 2, { trim: true })}</p>}
      <label className="inline-flex min-h-11 items-center gap-space-2 text-t13 text-fg md:min-h-9" data-save-default={CAPITAL_KEY}>
        <input type="checkbox" className="h-5 w-5 accent-brand" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />
        Save as my default
      </label>
      {problem && <p role="alert" className="text-t12 text-down">{problem}</p>}
    </div>
  );
}

export default CapitalAsk;

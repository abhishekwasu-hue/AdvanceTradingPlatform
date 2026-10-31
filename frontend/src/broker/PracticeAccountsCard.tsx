import { useEffect, useState, type FormEvent } from "react";
import { ApiError, friendlyError } from "../api/errors";
import { fnoApi, type PracticeAccount } from "../fno/api";
import { BTN_SECONDARY } from "./parts";
import { Details } from "../ui/Details";

/**
 * M45 MA-6 (`ui_v2` + `multi_account`): "Practice accounts" - the user's simulated PAPER accounts, so a multi-account
 * order can be tried on PAPER exactly as it will run later (MB-7). Name and capital; one Primary (moving it asks first);
 * Secondaries with a group; on / off; how each account sizes its order (lots x the Primary's, fixed lots, or % of its
 * capital). The order ticket's "Run on" then places one PAPER order per account. Never a broker order.
 */

const SIZING_TEXT: Record<PracticeAccount["sizing_mode"], string> = {
  MULTIPLIER: "× the Primary's lots", FIXED: "fixed lots", PCT_CAPITAL: "% of its capital",
};
const FIELD = "h-11 rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg";

function code(e: unknown): string | null {
  if (!(e instanceof ApiError) || !e.body) return null;
  try { return (JSON.parse(e.body) as { detail?: { code?: string } }).detail?.code ?? null; } catch { return null; }
}

export default function PracticeAccountsCard({ api = fnoApi }: { api?: Pick<typeof fnoApi, "practiceAccounts" | "addPracticeAccount" | "changePracticeAccount"> }) {
  const [rows, setRows] = useState<PracticeAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [capital, setCapital] = useState("");
  const [confirmFor, setConfirmFor] = useState<number | null>(null);

  function refresh() { api.practiceAccounts().then(setRows).catch((e) => setError(friendlyError(e))); }
  useEffect(refresh, [api]);

  async function act(fn: () => Promise<unknown>, id?: number) {
    setBusy(true); setError(null);
    try { await fn(); setConfirmFor(null); refresh(); } catch (e) {
      if (code(e) === "confirm_primary" && id !== undefined) setConfirmFor(id); else setError(friendlyError(e));
    } finally { setBusy(false); }
  }

  function add(ev: FormEvent) {
    ev.preventDefault();
    const amount = Number(capital.replace(/,/g, ""));
    if (!name.trim() || !(amount > 0)) { setError("Give the account a name and a capital above zero."); return; }
    void act(async () => { await api.addPracticeAccount(name.trim(), amount); setName(""); setCapital(""); });
  }

  return (
    <section aria-labelledby="practice-accounts-title" className="space-y-space-3" data-testid="practice-accounts">
      <h3 id="practice-accounts-title" className="text-t14 font-semibold text-fg">Practice accounts</h3>
      <Details id="broker.practice-accounts" className="mt-0">
        <p className="text-t12 text-fg-muted">
          Simulated accounts for trying one order on several accounts in practice mode. Choose them in the order ticket under
          "Run on". Each places its own practice order; nothing reaches a broker.
        </p>
      </Details>
      {error && <div role="alert" className="text-t12 text-down">{error}</div>}
      <ul className="space-y-space-2">
        {(rows ?? []).map((a) => (
          <li key={a.id} className="rounded-control border border-border p-space-3" data-testid={`practice-${a.id}`}>
            <div className="flex flex-wrap items-center gap-space-2">
              <span className="text-t14 font-semibold text-fg">{a.label}</span>
              {a.role && (
                <span className={`rounded-full border px-space-2 py-px text-t12 font-semibold ${a.role === "PRIMARY" ? "border-brand/50 text-brand" : "border-border text-fg"}`}>
                  {a.role === "PRIMARY" ? "Primary" : "Secondary"}{a.role === "SECONDARY" && a.group_name ? ` · ${a.group_name}` : ""}
                </span>
              )}
              <span className={`text-t12 ${a.enabled ? "text-up" : "text-warn"}`}>{a.enabled ? "On" : "Off - no new orders"}</span>
            </div>
            <div className="mt-space-1 text-t12 text-fg-muted font-tabular">
              Capital {a.capital.toLocaleString("en-IN", { maximumFractionDigits: 0 })} · {a.role === "PRIMARY" ? "the lots you enter in the ticket" : `${a.sizing_value} ${SIZING_TEXT[a.sizing_mode]}`}
            </div>
            {confirmFor === a.id ? (
              <div className="mt-space-2 flex flex-wrap items-center gap-space-2 text-t12 text-fg">
                <span>Make this the Primary? The current Primary becomes Secondary.</span>
                <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => act(() => api.changePracticeAccount(a.id, { role: "PRIMARY", confirm: true }), a.id)}>Yes, make Primary</button>
                <button type="button" className={BTN_SECONDARY} onClick={() => setConfirmFor(null)}>Cancel</button>
              </div>
            ) : (
              <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
                {a.role !== "PRIMARY" && <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => act(() => api.changePracticeAccount(a.id, { role: "PRIMARY" }), a.id)}>Make Primary</button>}
                {a.role !== "SECONDARY" && <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => act(() => api.changePracticeAccount(a.id, { role: "SECONDARY" }))}>Make Secondary</button>}
                <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => act(() => api.changePracticeAccount(a.id, { enabled: !a.enabled }))}>{a.enabled ? "Turn off" : "Turn on"}</button>
              </div>
            )}
            {a.role === "SECONDARY" && (
              <form className="mt-space-2 flex flex-wrap items-center gap-space-2" onSubmit={(ev) => {
                ev.preventDefault();
                const data = new FormData(ev.currentTarget);
                void act(() => api.changePracticeAccount(a.id, {
                  group_name: String(data.get("group") ?? ""), sizing_mode: String(data.get("mode")), sizing_value: Number(data.get("value")),
                }));
              }}>
                <label className="text-t12 text-fg-muted">Group <input name="group" defaultValue={a.group_name ?? ""} maxLength={50} className={`${FIELD} ml-space-1 w-32`} /></label>
                <label className="text-t12 text-fg-muted">Size <input name="value" defaultValue={a.sizing_value} inputMode="decimal" className={`${FIELD} ml-space-1 w-20`} /></label>
                <select name="mode" defaultValue={a.sizing_mode} aria-label="Sizing" className={FIELD}>
                  <option value="MULTIPLIER">× the Primary's lots</option>
                  <option value="FIXED">fixed lots</option>
                  <option value="PCT_CAPITAL">% of its capital</option>
                </select>
                <button type="submit" className={BTN_SECONDARY} disabled={busy}>Save</button>
              </form>
            )}
          </li>
        ))}
      </ul>
      <form className="flex flex-wrap items-end gap-space-2" onSubmit={add} data-testid="practice-add">
        <label className="text-t12 text-fg-muted">Name<input value={name} onChange={(e) => setName(e.target.value)} maxLength={50} className={`${FIELD} mt-space-1 block w-40`} /></label>
        <label className="text-t12 text-fg-muted">Capital<input value={capital} onChange={(e) => setCapital(e.target.value)} inputMode="numeric" className={`${FIELD} mt-space-1 block w-36`} /></label>
        <button type="submit" className={BTN_SECONDARY} disabled={busy}>Add practice account</button>
      </form>
    </section>
  );
}

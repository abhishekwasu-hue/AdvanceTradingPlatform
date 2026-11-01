import { useEffect, useState } from "react";
import { api } from "../api/client";
import { errorSentence } from "../api/errors";
import { BUTTON_SECONDARY } from "../trading/PartsV2";
import type { ContractPreview, ContractRules, DeploymentCreateRequest, ResolvedContract } from "../types";
import { Chip } from "../ui/Chip";
import { formatDateIST } from "../ui/format";

/**
 * UI-28 U7.4 (owner, 28 Oct; Streak users ask which contract a deploy will trade): before "Start on paper", one tap
 * shows the exact F&O contract each signal side would trade now - "NIFTY 24500 CE · 3 Nov · 1 lot (75)" - read from
 * the server's own picker (POST /deployments/preview-contract, the same rules the bot uses). Nothing starts; a cash
 * symbol (the underlying itself) needs no preview.
 */
const RULE_KEYS: (keyof ContractRules)[] = ["instrument_kind", "option_position", "expiry_rule", "strike_rule", "strike_offset",
  "premium_stop_pct", "max_lots", "strike_filters", "option_strategy", "spread_width", "target_credit_pct", "stop_credit_pct",
  "underlying_breach_exit", "custom_legs"];

export function rulesOf(request: DeploymentCreateRequest): ContractRules {
  const out: Record<string, unknown> = {};
  for (const k of RULE_KEYS) if ((request as unknown as Record<string, unknown>)[k] !== undefined) out[k] = (request as unknown as Record<string, unknown>)[k];
  return out as unknown as ContractRules;
}

/** "NIFTY 24500 CE · 3 Nov · 1 lot (75)" from the server's resolved contract. Pure. */
export function contractText(c: ResolvedContract, lots: number | null): string {
  const what = c.kind === "OPTION" && c.strike != null && c.right ? `${c.underlying} ${c.strike} ${c.right}` : c.tradingsymbol;
  const day = formatDateIST(c.expiry).replace(/ \d{4}$/, "");
  // M68 PR-2: no lots of the bot's own - its size comes from his Position size (lot size shown, never a guessed 1 lot)
  if (lots === null) return `${what} · ${day} · lot size ${c.lot_size} · size from your Position size`;
  return `${what} · ${day} · ${lots} lot${lots === 1 ? "" : "s"} (${lots * c.lot_size})`;
}

export function previewRows(p: ContractPreview, lots: number | null): { side: "LONG" | "SHORT"; text: string }[] {
  return (["LONG", "SHORT"] as const).flatMap((side) => {
    const c = p.contracts?.[side];
    return c && !("error" in c) ? [{ side, text: contractText(c, lots) }] : [];
  });
}

export default function ContractPreviewV2({ request }: { request: DeploymentCreateRequest }) {
  const key = JSON.stringify(request);
  const [state, setState] = useState<{ key: string; rows: { side: "LONG" | "SHORT"; text: string }[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setProblem(null); }, [key]);
  if (request.instrument_kind === "UNDERLYING" || !request.instrument_kind) return null;
  const rows = state?.key === key ? state.rows : null;
  async function look() {
    setBusy(true); setProblem(null);
    try {
      const p = await api.previewContract({ ...rulesOf(request), symbol: request.symbol });
      const out = previewRows(p, request.max_lots ?? null);
      setState({ key, rows: out });
      if (!out.length) setProblem("No contract matches these settings just now.");
    } catch (e) {
      setProblem(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-space-2" data-contract-preview>
      {rows && rows.length > 0 ? (
        <ul className="flex flex-wrap gap-space-2" aria-label="The contract it would trade now">
          {rows.map((r) => (
            <li key={r.side}><Chip tone="neutral" variant="meaning" name={`contract-${r.side.toLowerCase()}`}>
              {r.side === "LONG" ? "Long signal" : "Short signal"}: {r.text}
            </Chip></li>
          ))}
        </ul>
      ) : (
        <button type="button" className={BUTTON_SECONDARY} onClick={() => void look()} disabled={busy} data-contract-preview-open>
          {busy ? "Looking…" : "See the contract it would trade"}
        </button>
      )}
      {problem && <p role="status" className="text-t13 text-fg-muted">{problem}</p>}
    </div>
  );
}

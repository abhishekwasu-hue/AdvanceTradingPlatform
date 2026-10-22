import { Bell, Scale } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { request } from "../api/client";
import { watchlistsV2Api } from "../chart/watchlistsV2";
import { cx } from "../components/primitives/cx";
import { BUTTON_SECONDARY } from "../trading/PartsV2";
import { formatINR, formatNumber } from "../ui/format";
import type { Quotes } from "./CompareV2";
import {
  ALERT_ONLY, alertBody, alertChoices, alertSavedText, captureBar, keptChoice, premiumCapture, type AlertChoice, type Capture, type PosLeg,
} from "./positionInsights";

/**
 * F&O workspace 8b, slice 2 on the positions page (pure parts in positionInsights.ts): the premium kept so far on a
 * short-premium position (8b-e), "Tell me when" on the user's OWN levels (8b-f, an alert of the existing alert rules -
 * it only tells him) and the "Compare a change" button (8b-g, CompareV2.tsx). None of them is an order button, none is
 * read by one, and none ever stands in place of Exit now.
 */

export type CreateAlert = (body: Record<string, unknown>) => Promise<unknown>;
const createAlertRule: CreateAlert = (body) => request<unknown>("/alerts/rules", { method: "POST", body: JSON.stringify(body) });

const BUTTON = BUTTON_SECONDARY;

/** "You have kept ₹262.50 of the ₹2,625.00 premium received (10%)." (null kept: the live prices are still loading). */
export function captureText(c: Capture): string {
  if (c.kept === null || c.closeNow === null || c.pct === null) {
    return `Premium received ${formatINR(c.received)}. What you have kept shows once the live prices load.`;
  }
  if (c.kept >= 0) {
    return `You have kept ${formatINR(c.kept)} of the ${formatINR(c.received)} premium received (${formatNumber(c.pct, c.pct % 1 ? 1 : 0)}%). Closing now costs ${formatINR(c.closeNow)}.`;
  }
  return `The premium has grown: closing now costs ${formatINR(c.closeNow)}, ${formatINR(-c.kept)} more than the ${formatINR(c.received)} received.`;
}

/** 8b-e: the premium kept so far (information only). Null for anything that is not short premium. */
export function CaptureLineV2({ legs, daysLeft }: { legs: readonly PosLeg[]; daysLeft?: string | null }) {
  const c = premiumCapture(legs);
  if (!c) return null;
  const bar = captureBar(c);
  return (
    <div className="mt-space-2 space-y-space-1" data-capture={c.pct === null ? "waiting" : c.kept !== null && c.kept < 0 ? "grown" : "kept"}>
      <div className="flex items-baseline justify-between gap-space-2 text-t12 text-fg-muted">
        <span className="font-semibold">Premium kept so far</span>
        {daysLeft && <span>{daysLeft}</span>}
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-pill bg-surface-2" role="meter" aria-label="Premium kept so far" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(bar)}>
        <div className={cx("h-full rounded-pill", c.kept !== null && c.kept < 0 ? "bg-down" : "bg-up")} style={{ width: `${c.kept !== null && c.kept < 0 ? 100 : bar}%` }} />
      </div>
      <p className="text-t13 text-fg">{captureText(c)}</p>
    </div>
  );
}

/** "Tell me when": the alert choices of the user's own levels (no state; the tests render it). */
export function AlertChoicesView({ choices, picked, kept, keptLeg, note, busy, idPrefix, onPick, onKept, onSave }: {
  choices: readonly AlertChoice[]; picked: string | null; kept: string; keptLeg: PosLeg | null; note: { tone: "done" | "warn"; text: string } | null;
  busy: boolean; idPrefix: string; onPick: (key: string) => void; onKept: (text: string) => void; onSave: () => void;
}) {
  const keptPick = keptLeg ? keptChoice(keptLeg, kept) : null;
  const can = picked === "kept" ? !!keptPick : choices.some((c) => c.key === picked);
  return (
    <div className="mt-space-2 space-y-space-2 rounded-control border border-subtle p-space-3" data-structure-alert>
      <p className="text-t13 font-semibold text-fg">Tell me when</p>
      <div role="radiogroup" aria-label="Tell me when" className="space-y-space-1">
        {choices.map((c) => (
          <label key={c.key} className="flex min-h-11 cursor-pointer items-center gap-space-2 text-t14 text-fg" data-alert-choice={c.key}>
            <input type="radio" name={`${idPrefix}-alert`} checked={picked === c.key} onChange={() => onPick(c.key)} className="h-4 w-4 accent-brand" />
            <span className="font-tabular">{c.label}</span>
          </label>
        ))}
        {keptLeg && (
          <label className="flex min-h-11 flex-wrap items-center gap-space-2 text-t14 text-fg" data-alert-choice="kept">
            <input type="radio" name={`${idPrefix}-alert`} checked={picked === "kept"} onChange={() => onPick("kept")} className="h-4 w-4 accent-brand" />
            <span>I have kept</span>
            <input type="text" inputMode="decimal" value={kept} onChange={(e) => { onKept(e.target.value); onPick("kept"); }} aria-label="Share of the premium kept, in %"
              placeholder="%" className="h-11 w-16 rounded-control border border-border bg-surface-2 px-space-2 text-t14 font-tabular text-fg md:h-9" data-alert-kept />
            <span>% of the premium{keptPick ? ` (${keptLeg.label} at ${formatNumber(keptPick.level, 2)})` : ""}</span>
          </label>
        )}
      </div>
      {choices.length === 0 && !keptLeg && <p className="text-t13 text-fg-muted">This position has no level of its own to watch yet.</p>}
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" className={BUTTON} onClick={onSave} disabled={!can || busy} data-alert-save>
          <Bell size={16} aria-hidden />{busy ? "Saving…" : "Save alert"}
        </button>
        <span className="text-t12 text-fg-muted">{ALERT_ONLY}</span>
      </div>
      {note && <p role="status" className={cx("rounded-control px-space-3 py-space-2 text-t13", note.tone === "done" ? "bg-surface-2 text-fg" : "bg-warn-tint text-warn")} data-alert-note={note.tone}>{note.text}</p>}
    </div>
  );
}

/** 8b-f: the "Tell me when" button and its panel on one position or one group of legs. Reads the underlying's price
 * once when opened (for the direction of a level); saves ONE instrument rule of the existing alert rules on the user's
 * tap. The rule has no action: it only notifies. */
export function StructureAlertV2({ legs, createAlert = createAlertRule, quotes = watchlistsV2Api.quotes }: {
  legs: readonly PosLeg[]; createAlert?: CreateAlert; quotes?: Quotes;
}) {
  const [open, setOpen] = useState(false);
  const [spot, setSpot] = useState<number | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [kept, setKept] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "done" | "warn"; text: string } | null>(null);
  const id = useId();
  const first = legs[0];
  useEffect(() => {
    if (!open || !first) return undefined;
    let alive = true;
    quotes([{ symbol: first.spotSymbol, exchange: first.spotExchange }]).then((r) => {
      const ltp = r.items.find((q) => q.symbol === first.spotSymbol)?.ltp ?? null;
      if (alive) setSpot(typeof ltp === "number" && ltp > 0 ? ltp : null);
    }).catch(() => { if (alive) setSpot(null); });
    return () => { alive = false; };
  }, [open, first?.spotSymbol, first?.spotExchange]);                 // `quotes` is fixed for the page
  if (!first) return null;
  const choices = alertChoices(legs, spot);
  const keptLeg = legs.length === 1 && first.side === "SELL" && first.right !== "FUT" ? first : null;

  async function save() {
    const c = picked === "kept" && keptLeg ? keptChoice(keptLeg, kept) : choices.find((x) => x.key === picked) ?? null;
    if (!c) { setNote({ tone: "warn", text: picked === "kept" ? "Enter a share between 1 and 99%." : "Pick what to be told about." }); return; }
    setBusy(true);
    try {
      await createAlert(alertBody(c));
      setNote({ tone: "done", text: alertSavedText(c) });
    } catch (e) {
      setNote({ tone: "warn", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className={BUTTON} onClick={() => { setOpen((o) => !o); setNote(null); }} aria-expanded={open} data-testid="fno-alert">
        <Bell size={16} aria-hidden />Tell me when
      </button>
      {open && (
        <div className="basis-full">
          <AlertChoicesView choices={choices} picked={picked} kept={kept} keptLeg={keptLeg} note={note} busy={busy} idPrefix={id}
            onPick={setPicked} onKept={setKept} onSave={() => { void save(); }} />
        </div>
      )}
    </>
  );
}

/** 8b-g: opens the side-by-side comparison of the legs now and with a change the user builds. */
export function CompareButtonV2({ onOpen }: { onOpen: () => void }) {
  return (
    <button type="button" className={BUTTON} onClick={onOpen} data-testid="fno-compare">
      <Scale size={16} aria-hidden />Compare a change
    </button>
  );
}

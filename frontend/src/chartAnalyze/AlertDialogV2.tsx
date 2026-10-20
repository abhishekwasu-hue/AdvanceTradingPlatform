import { useEffect, useState } from "react";
import { api } from "../api/client";
import { friendlyError } from "../api/errors";
import { Button, Dialog, Input } from "../components/primitives";
import { APPROXIMATE_NOTE, bandCondition, draftErrors, ruleBody, ruleTimeframe } from "./alerts";

/**
 * Analyze a chart: the alert form behind "Set an alert on this area" and "Alert me when this happens". It creates an
 * ordinary alert rule (POST /api/alerts/rules) after the user checks the symbol and the prices; with Image only the
 * prices are read from a picture, and the form says so. An alert only reports that the condition matched - it never
 * places an order.
 */

export interface AlertTarget {
  title: string;
  symbol: string | null;
  timeframe: string | null;
  /** A price band (an area) - both ends editable. */
  band?: { low: number | null; high: number | null };
  /** A ready condition from the engine ("close > 120.5"). */
  condition?: string;
  approximate: boolean;
}

export default function AlertDialogV2({ target, onClose, onCreated }: { target: AlertTarget | null; onClose: () => void; onCreated: (text: string) => void }) {
  const [symbol, setSymbol] = useState("");
  const [low, setLow] = useState("");
  const [high, setHigh] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!target) return;
    setSymbol(target.symbol ?? "");
    setLow(target.band?.low != null ? String(target.band.low) : "");
    setHigh(target.band?.high != null ? String(target.band.high) : "");
    setError(null);
  }, [target]);

  if (!target) return null;
  const condition = target.band ? bandCondition(Number(low), Number(high)) ?? "" : target.condition ?? "";
  const draft = { symbol, condition, timeframe: ruleTimeframe(target.timeframe), name: `${target.title} - ${symbol.trim().toUpperCase()}` };
  const problems = draftErrors(draft);

  const save = async () => {
    if (problems.length) { setError(problems[0]); return; }
    setBusy(true);
    setError(null);
    try {
      await api.createAlertRule(ruleBody(draft));
      onCreated(`Alert created for ${draft.symbol.trim().toUpperCase()}: ${condition}`);
      onClose();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title={target.title}
      description="An alert tells you when the condition matches on a closed bar. It never places an order."
      footer={<>
        <Button className="h-11 md:h-9" onClick={onClose}>Cancel</Button>
        <Button variant="primary" className="h-11 md:h-9" loading={busy} onClick={save}>Save alert</Button>
      </>}>
      <div className="space-y-space-3">
        {target.approximate && <p className="rounded-control bg-warn-tint px-space-3 py-space-2 text-t12 text-fg" role="note">{APPROXIMATE_NOTE}</p>}
        <Input label="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} autoCapitalize="characters" className="h-11 md:h-9" />
        {target.band ? (
          <div className="grid grid-cols-2 gap-space-3">
            <Input label="From price" type="number" inputMode="decimal" value={low} onChange={(e) => setLow(e.target.value)} className="h-11 md:h-9" />
            <Input label="To price" type="number" inputMode="decimal" value={high} onChange={(e) => setHigh(e.target.value)} className="h-11 md:h-9" />
          </div>
        ) : null}
        <p className="text-t12 text-fg-muted">Condition: <span className="font-mono text-fg">{condition || "-"}</span> on {draft.timeframe} bars</p>
        {error && <p role="alert" className="text-t12 text-warn">{error}</p>}
      </div>
    </Dialog>
  );
}

/**
 * Z5 / P1-c: "Save strategy" - name the strategy and save it for the organisation, or load / delete a saved one.
 * Behind the operator flag `options_builder_saved` (off by default): while it is off the server answers 404 and the
 * page says saving is not turned on instead of offering a button that cannot work.
 */
import { useEffect, useState } from "react";
import { ApiError } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { Button, Dialog, Input } from "../components/primitives";
import { builderApi, wireLegs, type SavedInputs, type SavedStrategy, type SavedSummary } from "./api";
import type { Leg } from "./model";

export type SavedState = "unknown" | "off" | "on";

/** Whether saving is turned on for this organisation (a 404 from the list means the flag is off). */
export async function savedAvailability(): Promise<{ state: SavedState; list: SavedSummary[] }> {
  try {
    return { state: "on", list: await builderApi.saved() };
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 503)) return { state: "off", list: [] };
    throw e;
  }
}

// The roles the server lets save and delete (`require_trader`); a delete is also limited to whoever saved it, or the owner.
const TRADING_ROLES = ["OWNER", "USER", "STRATEGY_CREATOR"];

export function SaveDialog({ open, onOpenChange, underlying, inputs, legs, onLoad, errorText }: {
  open: boolean; onOpenChange: (open: boolean) => void; underlying: string; inputs: SavedInputs; legs: Leg[];
  onLoad: (s: SavedStrategy) => void; errorText: (e: unknown) => string;
}) {
  const { user } = useAuth();
  const canSave = !!user && TRADING_ROLES.includes(user.role);
  const canDelete = (s: SavedSummary) => canSave && (s.created_by === user?.id || user?.role === "OWNER");
  const [name, setName] = useState("");
  const [list, setList] = useState<SavedSummary[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setProblem(null);
    builderApi.saved().then(setList).catch((e: unknown) => setProblem(errorText(e)));
  }, [open, errorText]);

  const save = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const saved = await builderApi.save({
        name: name.trim(), underlying: underlying.trim(), inputs,
        legs: wireLegs(legs).map((l, i) => ({ ...l, ...(legs[i].premium_source ? { premium_source: legs[i].premium_source } : {}) })),
      });
      setList((all) => [saved, ...all]);
      setName("");
    } catch (e) {
      setProblem(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const load = async (id: number) => {
    try {
      onLoad(await builderApi.load(id));
      onOpenChange(false);
    } catch (e) {
      setProblem(errorText(e));
    }
  };
  const remove = async (id: number) => {
    try {
      await builderApi.remove(id);
      setList((all) => all.filter((s) => s.id !== id));
    } catch (e) {
      setProblem(errorText(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Save strategy" description="Saved strategies are shared with your organisation. Saving places no order.">
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <div className="min-w-0 flex-1">
          <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="e.g. Weekly iron condor" data-testid="save-name" />
        </div>
        <Button type="submit" variant="primary" disabled={!canSave || !name.trim() || !legs.length} loading={busy}>Save</Button>
      </form>
      {!canSave && <p className="mt-2 text-xs text-fg-muted">Your role can load saved strategies but not save them.</p>}
      {canSave && !legs.length && <p className="mt-2 text-xs text-fg-muted">Add a leg first.</p>}
      {problem && <p role="alert" className="mt-2 text-sm text-warn">{problem}</p>}
      <h3 className="mt-4 text-xs font-semibold text-fg-muted">Saved</h3>
      {list.length === 0 ? <p className="mt-1 text-sm text-fg-muted">Nothing saved yet.</p> : (
        <ul className="mt-1 divide-y divide-border/60" data-testid="saved-list">
          {list.map((s) => (
            <li key={s.id} className="flex items-center gap-2 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm text-fg">{s.name}</div>
                <div className="text-xs text-fg-muted">{[s.underlying, `${s.legs} legs`].filter(Boolean).join(" · ")}</div>
              </div>
              <Button size="sm" onClick={() => void load(s.id)}>Load</Button>
              {canDelete(s) && <Button size="sm" variant="ghost" onClick={() => void remove(s.id)} aria-label={`Delete ${s.name}`}>Delete</Button>}
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}

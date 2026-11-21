import { Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { request } from "../api/client";
import { ApiError, errorSentence } from "../api/errors";
import { Card } from "../components/ui";
import { Tabs } from "../components/primitives/Tabs";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";
import { formatDateTimeIST, formatSignedINR } from "../ui/format";

/**
 * SRV2 P2 + P4 ("Pending job purn kr" 21 Nov): the owner's 15M SRv2 bot settings per symbol, under the one "My trading agent" area,
 * and the bot's positions read-only.
 * - The fields, labels and bounds are the bot's own (the private package); this card only renders them and sends what he changed.
 * - Nothing is filled in for him: an unchanged field is not sent; the server checks every value with the bot's own rules.
 * - Positions are a read-only record of the bot's own PAPER store; exits stay the bot's own code.
 * Shown only to the owner account (the server answers 403 to every other admin).
 */
export type FieldType = "bool" | "int" | "float" | "choice" | "multi" | "text";
export interface AgentField {
  key: string; label: string; group: string; type: FieldType; choices?: string[] | null; unit?: string | null; help?: string | null;
  strikes?: boolean | number | null; min?: number | null; max?: number | null; value: unknown; saved: boolean;
}
export interface AgentSettings {
  installed: boolean; symbol: string; symbols: string[]; signal_symbols: string[]; fields: AgentField[]; has_fields?: boolean;
}
export interface AgentLeg { strike: number | null; option_type: string | null; side: string | null; qty: number | null; entry_price: number | null; exit_price: number | null }
export interface AgentPosition {
  id: number | string; symbol: string | null; direction: string | null; status: string | null; opened_at: string | null; closed_at: string | null;
  legs: AgentLeg[]; pnl: number | null; exit_reason: string | null; mode: string | null;
}

export const agentSettingsApi = {
  read: (symbol: string) => request<AgentSettings>(`/admin/controls/srv2-settings/${encodeURIComponent(symbol)}`),
  save: (symbol: string, values: Record<string, unknown>) => request<AgentSettings>(`/admin/controls/srv2-settings/${encodeURIComponent(symbol)}`,
    { method: "PUT", body: JSON.stringify({ values }) }),
  signalSymbols: (symbols: string[]) => request<{ signal_symbols: string[] }>("/admin/controls/srv2-signal-symbols",
    { method: "PUT", body: JSON.stringify({ symbols }) }),
  positions: () => request<{ installed: boolean; positions: AgentPosition[] }>("/admin/controls/srv2-positions"),
};

const GROUPS: { id: string; label: string }[] = [
  { id: "entry", label: "Entry" }, { id: "exit", label: "Exit" }, { id: "mode", label: "Engine & alerts" },
];

/** Only the fields he changed, in the type the field declares (a blank number is not sent). */
export function changedValues(fields: AgentField[], draft: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    if (!(f.key in draft)) continue;
    let v = draft[f.key];
    if (f.type === "int" || f.type === "float") {
      if (v === "" || v === null || v === undefined) continue;
      v = Number(v);
      if (!Number.isFinite(v)) continue;
    }
    if (JSON.stringify(v) !== JSON.stringify(f.value)) out[f.key] = v;
  }
  return out;
}

function FieldInput({ f, value, onChange, error }: { f: AgentField; value: unknown; onChange: (v: unknown) => void; error?: string }) {
  const id = `agent-field-${f.key}`;
  const base = "min-h-11 w-full rounded-control border bg-surface px-space-2 text-t14 text-fg md:min-h-9";
  const border = error ? "border-down" : "border-border";
  let control;
  if (f.type === "bool") {
    control = <input id={id} type="checkbox" className="h-5 w-5" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
  } else if (f.type === "choice") {
    control = <Combobox label={f.label} id={id} value={String(value ?? "")} onChange={onChange}
      options={(f.choices ?? []).map((c) => ({ value: c, label: c }))} />;
  } else if (f.type === "multi") {
    const cur = Array.isArray(value) ? (value as string[]) : [];
    control = (
      <div className="flex flex-wrap gap-space-2" role="group" aria-labelledby={`${id}-label`}>
        {(f.choices ?? []).map((c) => (
          <label key={c} className="inline-flex items-center gap-space-1 text-t14">
            <input type="checkbox" checked={cur.includes(c)}
              onChange={(e) => onChange(e.target.checked ? [...cur, c] : cur.filter((x) => x !== c))} /> {c}
          </label>
        ))}
      </div>
    );
  } else {
    const numeric = f.type === "int" || f.type === "float";
    control = <input id={id} className={`${base} ${border}`} inputMode={numeric ? "decimal" : undefined} type={numeric ? "number" : "text"}
      step={f.type === "float" ? "any" : undefined} min={f.min ?? undefined} max={f.max ?? undefined} value={value === null || value === undefined ? "" : String(value)}
      onChange={(e) => onChange(e.target.value)} aria-invalid={Boolean(error)} />;
  }
  return (
    <div className={f.type === "bool" ? "flex items-center justify-between gap-space-3 py-space-1" : "space-y-space-1 py-space-1"} data-agent-field={f.key}>
      <label id={`${id}-label`} htmlFor={id} className="min-w-0 text-t14 text-fg">
        {f.label}{f.unit ? <span className="text-fg-muted"> ({f.unit})</span> : null}
      </label>
      {f.type === "bool" ? control : <div className="md:max-w-xs">{control}</div>}
      {f.help && <p className="text-t12 text-fg-muted">{f.help}</p>}
      {error && <p className="text-t12 text-down" role="alert">{error}</p>}
    </div>
  );
}

export function SettingsForm({ settings, busy = false, errors = {}, onSave }: {
  settings: AgentSettings; busy?: boolean; errors?: Record<string, string>; onSave?: (values: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [tab, setTab] = useState("entry");
  useEffect(() => { setDraft({}); }, [settings]);
  const changed = useMemo(() => changedValues(settings.fields, draft), [settings.fields, draft]);
  const count = Object.keys(changed).length;
  const groups = GROUPS.filter((g) => settings.fields.some((f) => f.group === g.id));
  if (!settings.fields.length) return <p className="text-t13 text-fg-muted">The bot's settings list is not installed on this server yet.</p>;
  return (
    <div className="space-y-space-2" data-agent-settings={settings.symbol}>
      <Tabs value={groups.some((g) => g.id === tab) ? tab : groups[0]?.id ?? "entry"} onChange={setTab}
        items={groups.map((g) => ({
          value: g.id, label: g.label,
          content: (
            <div className="divide-y divide-border">
              {settings.fields.filter((f) => f.group === g.id).map((f) => (
                <FieldInput key={f.key} f={f} value={f.key in draft ? draft[f.key] : f.value} error={errors[f.key]}
                  onChange={(v) => setDraft((d) => ({ ...d, [f.key]: v }))} />
              ))}
            </div>
          ),
        }))} />
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" disabled={busy || count === 0} onClick={() => onSave?.(changed)}
          className="inline-flex min-h-11 items-center gap-space-2 rounded-control bg-brand px-space-3 text-t14 font-semibold text-on-brand disabled:opacity-50 md:min-h-9">
          <Save size={14} aria-hidden /> Save {settings.symbol}{count ? ` (${count})` : ""}
        </button>
        {count > 0 && <button type="button" className="text-t13 text-fg-muted underline" onClick={() => setDraft({})}>Undo changes</button>}
      </div>
    </div>
  );
}

/** The server's per-field sentences from a refused save (422 {"detail": {"errors": {...}}}), else null. */
export function fieldErrorsOf(e: unknown): Record<string, string> | null {
  const body = e instanceof ApiError ? e.body : "";
  try {
    const errors = (JSON.parse(body) as { detail?: { errors?: Record<string, string> } })?.detail?.errors;
    return errors && typeof errors === "object" ? errors : null;
  } catch {
    return null;
  }
}

function money(v: number | null): string {
  return formatSignedINR(v);
}

export function PositionsList({ positions }: { positions: AgentPosition[] }) {
  if (!positions.length) return <p className="text-t13 text-fg-muted">No positions yet.</p>;
  return (
    <ul className="divide-y divide-border border-y border-border" aria-label="The bot's positions" data-agent-positions>
      {positions.map((p) => {
        const open = p.status === "OPEN";
        const tone = p.pnl === null || p.pnl === undefined ? "text-fg-muted" : p.pnl >= 0 ? "text-up" : "text-down";
        return (
          <li key={p.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-space-2 py-space-1 text-t13" data-agent-position={p.status ?? ""}>
            <span className="min-w-0 truncate">
              <span className="font-semibold text-fg">{p.symbol}</span>
              <span className="text-fg-muted"> · {(p.legs ?? []).map((l) => `${l.side === "SELL" ? "−" : "+"}${l.strike ?? ""}${l.option_type ?? ""}`).join(" ")}</span>
            </span>
            <span className={`whitespace-nowrap font-semibold ${tone}`}>{money(p.pnl)}</span>
            <span className="text-t12 text-fg-muted">
              {open ? "Open" : `Closed${p.exit_reason ? ` · ${p.exit_reason}` : ""}`}
              {" · "}{p.opened_at ? formatDateTimeIST(p.opened_at, { year: false }) : ""}{p.mode ? ` · ${p.mode}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default function AgentSettingsCard() {
  const [symbol, setSymbol] = useState<string | null>(null);
  const [settings, setSettings] = useState<AgentSettings | null>(null);
  const [positions, setPositions] = useState<AgentPosition[]>([]);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    agentSettingsApi.read(symbol ?? "NIFTY").then((s) => { setSettings(s); setDenied(false); }, () => setDenied(true));
  }, [symbol]);
  useEffect(() => { agentSettingsApi.positions().then((r) => setPositions(r.positions), () => setPositions([])); }, []);

  async function save(values: Record<string, unknown>) {
    if (!settings) return;
    setBusy(true); setNote(null); setErrors({});
    try {
      setSettings(await agentSettingsApi.save(settings.symbol, values));
      setNote(`${settings.symbol} settings saved.`);
    } catch (e) {
      const fieldErrors = fieldErrorsOf(e);
      if (fieldErrors) setErrors(fieldErrors);
      else setNote(errorSentence(e));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSignal(sym: string, on: boolean) {
    if (!settings) return;
    const next = on ? [...settings.signal_symbols, sym] : settings.signal_symbols.filter((s) => s !== sym);
    try {
      const r = await agentSettingsApi.signalSymbols(next);
      setSettings({ ...settings, signal_symbols: r.signal_symbols });
    } catch (e) {
      setNote(errorSentence(e));
    }
  }

  if (denied || !settings?.installed) return null;
  return (
    <div data-console-card="agent-settings"><Card title="My trading agent: settings and positions">
      <div className="space-y-space-3 text-t13">
        <div className="flex flex-wrap items-end gap-space-3">
          <div className="w-40"><Combobox label="Symbol" value={settings.symbol} onChange={setSymbol}
            options={settings.symbols.map((s) => ({ value: s, label: s }))} /></div>
          <div className="flex flex-wrap items-center gap-space-2" role="group" aria-label="Symbols the bot reads">
            <span className="text-fg-muted">Reads:</span>
            {settings.symbols.map((s) => (
              <label key={s} className="inline-flex items-center gap-space-1">
                <input type="checkbox" checked={settings.signal_symbols.includes(s)} onChange={(e) => void toggleSignal(s, e.target.checked)} /> {s}
              </label>
            ))}
          </div>
        </div>
        {note && <p role="status" className="text-fg">{note}</p>}
        <SettingsForm settings={settings} busy={busy} errors={errors} onSave={(v) => void save(v)} />
        <h3 className="pt-space-2 text-t14 font-semibold text-fg">Positions</h3>
        <PositionsList positions={positions} />
        <Details id="admin.agent-settings" className="mt-0">
          These are the bot's own settings, per symbol. Only what you change is saved, and the bot checks every value with its own
          rules. Positions are a record of what the bot did; its exits always run.
        </Details>
      </div>
    </Card></div>
  );
}

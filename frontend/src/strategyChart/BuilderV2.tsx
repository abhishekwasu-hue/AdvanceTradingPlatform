import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Sheet } from "../components/primitives/Dialog";
import { cx } from "../components/primitives/cx";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import {
  CMP_LABEL, MAX_DEPTH, blankSettings, PRICE_FIELDS, TIMEFRAMES, TWO_SIDED, builderApi, depthOf, emptyDraft, exportFile, newCondition, nextId, numberOrNull,
  operandKind, parseImport, serialize, toDraft, type Block, type BlockArg, type Cmp, type Cond, type Draft, type Group,
  type LevelRule, type Operand, type SavedStrategy, type Trailing,
} from "./builder";

/**
 * SFC P5 (flag `strategy_chart`): the Strategy Builder drawer on the Charts page (right side; a full-height sheet on a
 * phone). Every block ATP computes on bars, AND / OR groups, a timeframe per condition, the entry window and limit, the
 * exits and the options legs. DEF-0: nothing is pre-filled. Save / save as a copy / delete / export / import; "Show on
 * chart" draws it at once. Nothing is ordered: a saved strategy runs nowhere until he deploys it himself.
 */
const FIELD = "min-h-11 rounded-control border border-subtle bg-surface-1 px-space-2 text-t14 text-fg md:min-h-9";
const SMALL_BTN = "inline-flex min-h-11 items-center rounded-control px-space-2 text-t13 md:min-h-9";

function Field({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={cx("flex min-w-0 flex-col gap-space-1 text-t12 text-fg-muted", className)}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function NumberBox({ value, onChange, label, step = "any", min }: { value: number | null | undefined; onChange: (v: number | null) => void; label: string;
  step?: string; min?: number }) {
  return (
    <input type="number" inputMode="decimal" step={step} min={min} aria-label={label} value={value ?? ""} placeholder="—"
      onChange={(e) => onChange(numberOrNull(e.target.value))} className={cx(FIELD, "w-24")} />
  );
}

// --- operands -------------------------------------------------------------------------------------------------------

function ArgBox({ arg, value, onChange }: { arg: BlockArg; value: unknown; onChange: (v: string | number | boolean | "") => void }) {
  const label = arg.name;
  if (arg.series) {
    // a number series: one of the bar's own values (or a fixed number)
    const isField = typeof value === "string" && (PRICE_FIELDS as readonly string[]).includes(value);
    return (
      <span className="inline-flex items-center gap-space-1">
        <select aria-label={label} value={isField ? String(value) : typeof value === "number" ? "__num" : ""} className={FIELD}
          onChange={(e) => onChange(e.target.value === "" ? "" : e.target.value === "__num" ? (typeof value === "number" ? value : "") : e.target.value)}>
          <option value="">{arg.name}…</option>
          {PRICE_FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}
          <option value="__num">a number</option>
        </select>
        {typeof value === "number" && <input type="number" aria-label={`${label} number`} value={value} className={cx(FIELD, "w-20")}
          onChange={(e) => { const n = numberOrNull(e.target.value); onChange(n === null ? "" : n); }} />}
      </span>
    );
  }
  if (arg.choices.length) {
    return (
      <select aria-label={label} value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value)} className={FIELD}>
        <option value="">{arg.name}…</option>
        {arg.choices.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    );
  }
  if (arg.type === "bool") {
    return (
      <select aria-label={label} value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? "" : e.target.value === "true")} className={FIELD}>
        <option value="">{arg.name}…</option><option value="true">yes</option><option value="false">no</option>
      </select>
    );
  }
  if (arg.type === "num" || arg.type === "window") {
    return (
      <input type="number" inputMode="decimal" aria-label={label} placeholder={arg.name} value={value === undefined ? "" : String(value)}
        onChange={(e) => { const n = numberOrNull(e.target.value); onChange(n === null ? "" : n); }} className={cx(FIELD, "w-24")} />
    );
  }
  return <input type="text" aria-label={label} placeholder={arg.name} value={value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value)} className={cx(FIELD, "w-28")} />;
}

function OperandEditor({ value, onChange, blocks, want, label }: { value: Operand | null | undefined; onChange: (o: Operand | null) => void; blocks: Block[];
  want: "number" | "flag"; label: string }) {
  const options = blocks.filter((b) => (want === "flag" ? b.returns === "bool" : b.returns === "num"));
  const pick = value ? (value.kind === "value" ? "__value" : value.kind === "price" ? `__price:${value.field}` : value.block) : "";
  const spec = value && (value.kind === "indicator" || value.kind === "pa") ? blocks.find((b) => b.block === value.block) : undefined;
  const choose = (v: string) => {
    if (!v) return onChange(null);
    if (v === "__value") return onChange({ kind: "value", value: null });
    if (v.startsWith("__price:")) return onChange({ kind: "price", field: v.slice(8) as (typeof PRICE_FIELDS)[number] });
    const b = blocks.find((x) => x.block === v);
    if (b) onChange({ kind: operandKind(b), block: b.block, params: {} });
  };
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-space-1" data-operand={label}>
      <select aria-label={label} value={pick} onChange={(e) => choose(e.target.value)} className={cx(FIELD, "max-w-[14rem]")}>
        <option value="">Choose…</option>
        {want === "number" && <option value="__value">A number</option>}
        {want === "number" && <optgroup label="Price">{PRICE_FIELDS.map((f) => <option key={f} value={`__price:${f}`}>{f}</option>)}</optgroup>}
        <optgroup label={want === "flag" ? "Price action and events" : "Indicators"}>
          {options.map((b) => <option key={b.block} value={b.block} title={b.doc}>{b.block}</option>)}
        </optgroup>
      </select>
      {value?.kind === "value" && <NumberBox label={`${label} number`} value={value.value} onChange={(n) => onChange({ kind: "value", value: n })} />}
      {spec && value && (value.kind === "indicator" || value.kind === "pa") && [...spec.args, ...spec.kwargs].map((a) => (
        <ArgBox key={a.name} arg={a} value={value.params[a.name]} onChange={(v) => {
          const params = { ...value.params };
          if (v === "") delete params[a.name]; else params[a.name] = v;
          onChange({ ...value, params });
        }} />
      ))}
    </span>
  );
}

// --- conditions and groups ------------------------------------------------------------------------------------------

function CondEditor({ cond, onChange, onRemove, blocks, timeframe }: { cond: Cond; onChange: (c: Cond) => void; onRemove: () => void; blocks: Block[];
  timeframe: string | null }) {
  const flagLeft = cond.left?.kind === "pa";
  const cmps: Cmp[] = flagLeft ? ["is_true"] : ["crosses_above", "crosses_below", "gt", "lt", "gte", "lte", "between", "rising", "falling"];
  return (
    <div className="flex flex-wrap items-center gap-space-2 rounded-control border border-subtle bg-surface-2 p-space-2" data-condition={cond.id}>
      <OperandEditor label={`${cond.id} left`} value={cond.left} blocks={blocks} want={cond.cmp === "is_true" || flagLeft ? "flag" : "number"}
        onChange={(left) => onChange({ ...cond, left, cmp: left?.kind === "pa" ? "is_true" : cond.cmp === "is_true" ? null : cond.cmp })} />
      {!flagLeft && (
        <select aria-label={`${cond.id} comparison`} value={cond.cmp ?? ""} onChange={(e) => onChange({ ...cond, cmp: (e.target.value || null) as Cmp | null })} className={FIELD}>
          <option value="">compare…</option>
          {cmps.map((c) => <option key={c} value={c}>{CMP_LABEL[c]}</option>)}
        </select>
      )}
      {flagLeft && <span className="text-t13 text-fg-muted">{CMP_LABEL.is_true}</span>}
      {cond.cmp && TWO_SIDED.includes(cond.cmp) && <OperandEditor label={`${cond.id} right`} value={cond.right} blocks={blocks} want="number" onChange={(right) => onChange({ ...cond, right })} />}
      {cond.cmp === "between" && <>
        <OperandEditor label={`${cond.id} low`} value={cond.low} blocks={blocks} want="number" onChange={(low) => onChange({ ...cond, low })} />
        <span className="text-t13 text-fg-muted">and</span>
        <OperandEditor label={`${cond.id} high`} value={cond.high} blocks={blocks} want="number" onChange={(high) => onChange({ ...cond, high })} />
      </>}
      {(cond.cmp === "rising" || cond.cmp === "falling") && <><NumberBox label={`${cond.id} bars`} value={cond.bars} min={1} step="1" onChange={(bars) => onChange({ ...cond, bars })} /><span className="text-t13 text-fg-muted">bars</span></>}
      <Field label="within bars" className="w-24"><NumberBox label={`${cond.id} within bars`} value={cond.within_bars} min={1} step="1" onChange={(within_bars) => onChange({ ...cond, within_bars })} /></Field>
      <Field label="on timeframe" className="w-28">
        <select aria-label={`${cond.id} timeframe`} value={cond.timeframe ?? ""} onChange={(e) => onChange({ ...cond, timeframe: e.target.value || null })} className={FIELD}>
          <option value="">{timeframe ? `Same (${timeframe})` : "Same"}</option>
          {TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </Field>
      <button type="button" onClick={onRemove} aria-label={`Remove condition ${cond.id}`} className={cx(SMALL_BTN, "ml-auto text-fg-muted hover:bg-surface-1")}>×</button>
    </div>
  );
}

function GroupEditor({ group, onChange, onRemove, draft, path, blocks, label }: { group: Group; onChange: (g: Group) => void; onRemove?: () => void;
  draft: Draft; path: number[]; blocks: Block[]; label: string }) {
  const set = (i: number, item: Cond | Group) => onChange({ ...group, items: group.items.map((x, k) => (k === i ? item : x)) });
  const drop = (i: number) => onChange({ ...group, items: group.items.filter((_, k) => k !== i) });
  return (
    <div className={cx("space-y-space-2", path.length > 0 && "rounded-control border border-dashed border-subtle p-space-2")} data-group={label}>
      <div className="flex flex-wrap items-center gap-space-2">
        <span className="text-t12 text-fg-muted">Join with</span>
        <div role="group" aria-label={`${label}: join with`} className="inline-flex rounded-control border border-subtle">
          {(["AND", "OR"] as const).map((op) => (
            <button key={op} type="button" aria-pressed={group.op === op} onClick={() => onChange({ ...group, op })}
              className={cx(SMALL_BTN, group.op === op ? "bg-brand-tint text-brand" : "text-fg-muted")}>{op === "AND" ? "All (AND)" : "Any (OR)"}</button>
          ))}
        </div>
        {onRemove && <button type="button" onClick={onRemove} className={cx(SMALL_BTN, "ml-auto text-fg-muted hover:bg-surface-2")}>Remove group</button>}
      </div>
      {group.items.map((item, i) => ("op" in item
        ? <GroupEditor key={i} group={item} draft={draft} path={[...path, i]} blocks={blocks} label={`${label} group ${i + 1}`}
            onChange={(g) => set(i, g)} onRemove={() => drop(i)} />
        : <CondEditor key={item.id} cond={item} blocks={blocks} timeframe={draft.timeframe} onChange={(c) => set(i, c)} onRemove={() => drop(i)} />))}
      <div className="flex flex-wrap gap-space-2">
        <button type="button" data-add-condition={label} onClick={() => onChange({ ...group, items: [...group.items, newCondition(nextId(draft))] })}
          className={cx(SMALL_BTN, "text-brand hover:bg-surface-2")}>+ Condition</button>
        {depthOf(path) < MAX_DEPTH && (
          <button type="button" onClick={() => onChange({ ...group, items: [...group.items, { op: group.op === "AND" ? "OR" : "AND", items: [newCondition(nextId(draft))] }] })}
            className={cx(SMALL_BTN, "text-brand hover:bg-surface-2")}>+ Group</button>
        )}
      </div>
    </div>
  );
}

// --- exits ----------------------------------------------------------------------------------------------------------

const LEVEL_KINDS: { value: LevelRule["kind"]; label: string }[] = [
  { value: "points", label: "Points" }, { value: "pct", label: "Percent" }, { value: "atr", label: "ATR ×" }, { value: "swing", label: "Swing" },
  { value: "candle", label: "Candle" }, { value: "premium", label: "Premium %" },
];

function LevelEditor({ label, value, onChange, trailing = false }: { label: string; value: LevelRule | Trailing | null; onChange: (v: LevelRule | Trailing | null) => void;
  trailing?: boolean }) {
  const kinds = trailing ? LEVEL_KINDS.filter((k) => ["points", "pct", "atr", "swing"].includes(k.value)) : LEVEL_KINDS;
  const v = value as LevelRule | null;
  return (
    <div className="flex flex-wrap items-end gap-space-2" data-exit={label}>
      <Field label={label} className="w-32">
        <select aria-label={label} value={v?.kind ?? ""} onChange={(e) => onChange(e.target.value ? { kind: e.target.value as LevelRule["kind"] } : null)} className={FIELD}>
          <option value="">Off</option>
          {kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
      </Field>
      {v && ["points", "pct", "atr", "premium"].includes(v.kind) && <Field label="value"><NumberBox label={`${label} value`} value={v.value} onChange={(value) => onChange({ ...v, value })} /></Field>}
      {v?.kind === "atr" && <Field label="ATR length"><NumberBox label={`${label} ATR length`} step="1" min={1} value={v.atr_period} onChange={(atr_period) => onChange({ ...v, atr_period })} /></Field>}
      {v?.kind === "swing" && <Field label="swing degree (0-3)"><NumberBox label={`${label} swing degree`} step="1" min={0} value={v.swing_degree} onChange={(swing_degree) => onChange({ ...v, swing_degree })} /></Field>}
      {v?.kind === "candle" && (
        <Field label="candle">
          <select aria-label={`${label} candle`} value={v.candle ?? ""} onChange={(e) => onChange({ ...v, candle: (e.target.value || null) as LevelRule["candle"] })} className={FIELD}>
            <option value="">Choose…</option><option value="entry">entry candle</option><option value="signal">signal candle</option><option value="previous">previous candle</option>
          </select>
        </Field>
      )}
    </div>
  );
}

function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="space-y-space-2 border-t border-subtle pt-space-3" data-builder-section={testId}>
      <h3 className="text-t14 font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

// --- the drawer -----------------------------------------------------------------------------------------------------

export function StrategyBuilderV2({ open, onOpenChange, editId, onSaved, onDeleted, api = builderApi }: {
  open: boolean; onOpenChange: (open: boolean) => void; editId: number | null;
  onSaved: (saved: SavedStrategy) => void; onDeleted: (id: string) => void; api?: typeof builderApi;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [recordId, setRecordId] = useState<number | null>(null);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importText, setImportText] = useState("");

  useEffect(() => {
    if (!open || blocks.length) return;
    api.blocks().then((r) => setBlocks(r.blocks), () => setBlocks([]));
  }, [open, api, blocks.length]);
  const loaded = useRef<number | null>(null);          // the record this drawer holds (a save keeps it; no reload)
  loaded.current = recordId;
  useEffect(() => {
    if (!open) return;
    if (editId !== null && editId === loaded.current) return;
    setNote(null); setProblems([]); setImporting(false);
    if (editId === null) { setDraft(emptyDraft()); setRecordId(null); return; }
    api.read(editId).then((s) => { setDraft(toDraft(s.strategy as unknown as Record<string, unknown>)); setRecordId(s.record_id); },
      () => setNote("This strategy could not be opened."));
  }, [open, editId, api]);

  // the server's own check, a moment after the last change (never saves)
  const body = useMemo(() => serialize(draft), [draft]);
  const timer = useRef<number | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.check(body).then((r) => setProblems(r.problems), () => setProblems([]));
    }, 600);
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [body, open, api]);

  const save = async (copy: boolean) => {
    setBusy(true); setNote(null);
    try {
      const payload = copy ? { ...body, name: `${draft.name.trim()} (copy)`.trim() } : body;
      const saved = recordId !== null && !copy ? await api.update(recordId, payload) : await api.create(payload);
      setRecordId(saved.record_id); setDraft(toDraft(saved.strategy as unknown as Record<string, unknown>));
      setNote(copy ? "Saved as a copy." : "Saved.");
      onSaved(saved);
    } catch (e) {
      setNote((e as Error).message || "It could not be saved.");
    } finally { setBusy(false); }
  };
  const remove = async () => {
    if (recordId === null || !window.confirm(`Delete "${draft.name}"? This cannot be undone.`)) return;
    setBusy(true);
    try { await api.remove(recordId); onDeleted(`custom:${recordId}`); onOpenChange(false); }
    catch (e) { setNote((e as Error).message || "It could not be deleted."); }
    finally { setBusy(false); }
  };
  const download = () => {
    const f = exportFile(draft);
    const url = URL.createObjectURL(new Blob([f.text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = f.name; a.click();
    URL.revokeObjectURL(url);
  };
  const doImport = () => {
    const got = parseImport(importText);
    if ("error" in got) { setNote(got.error); return; }
    setDraft(got.draft); setRecordId(null); setImporting(false); setImportText(""); setNote("Imported. Save it to keep it.");
  };

  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const setEntry = (patch: Partial<Draft["entry"]>) => setDraft((d) => ({ ...d, entry: { ...d.entry, ...patch } }));
  const setExit = (patch: Partial<Draft["exit"]>) => setDraft((d) => ({ ...d, exit: { ...d.exit, ...patch } }));
  const sides = draft.entry.sides;
  const blanks = useMemo(() => blankSettings(draft, blocks), [draft, blocks]);
  const shown = [...(draft.name.trim() ? [] : ["Give it a name"]), ...(draft.timeframe ? [] : ["Choose its timeframe"]), ...blanks,
    ...problems.filter((p) => !blanks.length || !/needs its|choose|enter the/i.test(p))];
  const canSave = shown.length === 0 && !busy;

  const footer = (
    <div className="space-y-space-2" data-builder-footer>
      {shown.length > 0 && (
        <ul className="max-h-28 space-y-space-1 overflow-y-auto text-t12 text-fg-muted" data-builder-problems aria-live="polite">
          {shown.slice(0, 8).map((p) => <li key={p}>• {p}</li>)}
          {shown.length > 8 && <li>… and {shown.length - 8} more</li>}
        </ul>
      )}
      {note && <p className="text-t12 text-fg-muted" role="status" data-builder-note>{note}</p>}
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" disabled={!canSave} onClick={() => void save(false)} data-builder-save
          className="inline-flex min-h-11 items-center rounded-control bg-brand px-space-4 text-t14 font-semibold text-on-brand disabled:opacity-50 md:min-h-9">Save</button>
        {recordId !== null && <button type="button" disabled={!canSave} onClick={() => void save(true)} className={cx(SMALL_BTN, "text-brand hover:bg-surface-2 disabled:opacity-50")}>Save as a copy</button>}
        <button type="button" onClick={download} className={cx(SMALL_BTN, "text-fg-muted hover:bg-surface-2")}>Export</button>
        <button type="button" onClick={() => setImporting((v) => !v)} className={cx(SMALL_BTN, "text-fg-muted hover:bg-surface-2")}>Import</button>
        {recordId !== null && <button type="button" onClick={() => void remove()} className={cx(SMALL_BTN, "ml-auto text-down hover:bg-surface-2")} data-builder-delete>Delete</button>}
      </div>
    </div>
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange} side={phone ? "bottom" : "right"} wide tall={phone}
      title={recordId !== null ? `Edit: ${draft.name || "strategy"}` : "New strategy"}
      description="Your own rules. Blank = off; nothing is filled in for you. Saving runs nothing - it only draws on the chart."
      footer={footer}>
      <div className="space-y-space-3" data-strategy-builder>
        {importing && (
          <div className="space-y-space-2 rounded-control border border-subtle p-space-2" data-builder-import>
            <textarea aria-label="Strategy file (JSON)" value={importText} onChange={(e) => setImportText(e.target.value)} rows={5}
              placeholder="Paste an exported strategy (atp.strategy/2)" className={cx(FIELD, "w-full font-mono text-t12")} />
            <button type="button" onClick={doImport} className={cx(SMALL_BTN, "text-brand hover:bg-surface-2")}>Use this</button>
          </div>
        )}
        <div className="flex flex-wrap items-end gap-space-3">
          <Field label="Name" className="min-w-[12rem] flex-1">
            <input type="text" value={draft.name} onChange={(e) => set({ name: e.target.value })} maxLength={80} className={FIELD} data-builder-name />
          </Field>
          <Field label="Timeframe">
            <select value={draft.timeframe ?? ""} onChange={(e) => set({ timeframe: e.target.value || null })} className={FIELD} data-builder-timeframe>
              <option value="">Choose…</option>
              {TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
          <div role="radiogroup" aria-label="Sides" className="inline-flex rounded-control border border-subtle">
            {(["long", "short", "both"] as const).map((s) => (
              <button key={s} type="button" role="radio" aria-checked={sides === s} data-builder-side={s}
                onClick={() => setEntry({ sides: s, long: s !== "short" ? draft.entry.long ?? { op: "AND", items: [] } : draft.entry.long,
                  short: s !== "long" ? draft.entry.short ?? { op: "AND", items: [] } : draft.entry.short })}
                className={cx(SMALL_BTN, sides === s ? "bg-brand-tint text-brand" : "text-fg-muted")}>{s === "long" ? "Long" : s === "short" ? "Short" : "Both"}</button>
            ))}
          </div>
        </div>

        {sides !== "short" && draft.entry.long && (
          <Section title="Long entry when" testId="entry-long">
            <GroupEditor group={draft.entry.long} draft={draft} path={[]} blocks={blocks} label="Long entry"
              onChange={(g) => setEntry({ long: g })} />
          </Section>
        )}
        {sides !== "long" && draft.entry.short && (
          <Section title="Short entry when" testId="entry-short">
            <GroupEditor group={draft.entry.short} draft={draft} path={[]} blocks={blocks} label="Short entry"
              onChange={(g) => setEntry({ short: g })} />
          </Section>
        )}

        <Section title="Entry limits (optional)" testId="limits">
          <div className="flex flex-wrap items-end gap-space-3">
            <Field label="Window from"><input type="time" value={draft.entry.window?.from ?? ""} className={FIELD} aria-label="Window from"
              onChange={(e) => setEntry({ window: e.target.value || draft.entry.window?.to ? { from: e.target.value, to: draft.entry.window?.to ?? "" } : null })} /></Field>
            <Field label="to"><input type="time" value={draft.entry.window?.to ?? ""} className={FIELD} aria-label="Window to"
              onChange={(e) => setEntry({ window: e.target.value || draft.entry.window?.from ? { from: draft.entry.window?.from ?? "", to: e.target.value } : null })} /></Field>
            <Field label="Max entries per day"><NumberBox label="Max entries per day" step="1" min={1} value={draft.entry.max_entries_per_day}
              onChange={(n) => setEntry({ max_entries_per_day: n })} /></Field>
          </div>
        </Section>

        <Section title="Exit (blank = off)" testId="exit">
          <LevelEditor label="Stop" value={draft.exit.stop} onChange={(v) => setExit({ stop: v as LevelRule | null })} />
          <LevelEditor label="Target" value={draft.exit.target} onChange={(v) => setExit({ target: v as LevelRule | null })} />
          <LevelEditor label="Trailing" trailing value={draft.exit.trailing} onChange={(v) => setExit({ trailing: v as Trailing | null })} />
          <div className="flex flex-wrap items-end gap-space-3">
            <Field label="Time exit after bars"><NumberBox label="Time exit after bars" step="1" min={1} value={draft.exit.time_exit?.after_bars}
              onChange={(n) => setExit({ time_exit: n || draft.exit.time_exit?.at ? { after_bars: n, at: draft.exit.time_exit?.at ?? null } : null })} /></Field>
            <Field label="or at"><input type="time" aria-label="Time exit at" value={draft.exit.time_exit?.at ?? ""} className={FIELD}
              onChange={(e) => setExit({ time_exit: e.target.value || draft.exit.time_exit?.after_bars ? { after_bars: draft.exit.time_exit?.after_bars ?? null, at: e.target.value || null } : null })} /></Field>
          </div>
          {(["long", "short"] as const).filter((s) => sides === "both" || sides === s).map((s) => {
            const key = s === "long" ? "long_conditions" : "short_conditions";
            const g = draft.exit[key];
            return g ? (
              <GroupEditor key={s} group={g} draft={draft} path={[]} blocks={blocks} label={`${s === "long" ? "Long" : "Short"} exit`}
                onChange={(next) => setExit({ [key]: next } as Partial<Draft["exit"]>)} onRemove={() => setExit({ [key]: null } as Partial<Draft["exit"]>)} />
            ) : (
              <button key={s} type="button" className={cx(SMALL_BTN, "text-brand hover:bg-surface-2")}
                onClick={() => setExit({ [key]: { op: "AND", items: [newCondition(nextId(draft))] } } as Partial<Draft["exit"]>)}>
                + Exit {s === "long" ? "a long" : "a short"} when…
              </button>
            );
          })}
        </Section>

        <Section title="Options legs (optional)" testId="options">
          {(draft.options?.legs ?? []).map((leg, i) => (
            <div key={i} className="flex flex-wrap items-center gap-space-2" data-option-leg={i}>
              <select aria-label={`Leg ${i + 1} action`} value={leg.action} className={FIELD}
                onChange={(e) => set({ options: { ...draft.options!, legs: draft.options!.legs.map((l, k) => (k === i ? { ...l, action: e.target.value as "buy" | "sell" } : l)) } })}>
                <option value="buy">Buy</option><option value="sell">Sell</option>
              </select>
              <select aria-label={`Leg ${i + 1} right`} value={leg.right} className={FIELD}
                onChange={(e) => set({ options: { ...draft.options!, legs: draft.options!.legs.map((l, k) => (k === i ? { ...l, right: e.target.value as "CE" | "PE" } : l)) } })}>
                <option value="CE">CE</option><option value="PE">PE</option>
              </select>
              <Field label="× lots ratio"><NumberBox label={`Leg ${i + 1} ratio`} step="1" min={1} value={leg.ratio}
                onChange={(ratio) => set({ options: { ...draft.options!, legs: draft.options!.legs.map((l, k) => (k === i ? { ...l, ratio } : l)) } })} /></Field>
              <Field label="strike steps from the chosen strike"><NumberBox label={`Leg ${i + 1} strike offset`} step="1" value={leg.strike_offset}
                onChange={(strike_offset) => set({ options: { ...draft.options!, legs: draft.options!.legs.map((l, k) => (k === i ? { ...l, strike_offset } : l)) } })} /></Field>
              <button type="button" aria-label={`Remove leg ${i + 1}`} className={cx(SMALL_BTN, "text-fg-muted")}
                onClick={() => { const legs = draft.options!.legs.filter((_, k) => k !== i); set({ options: legs.length ? { ...draft.options!, legs } : null }); }}>×</button>
            </div>
          ))}
          {(draft.options?.legs.length ?? 0) < 4 && (
            <button type="button" className={cx(SMALL_BTN, "text-brand hover:bg-surface-2")}
              onClick={() => set({ options: { legs: [...(draft.options?.legs ?? []), { action: "buy", right: "CE", ratio: null, strike_offset: null }],
                strike: draft.options?.strike ?? null, expiry: draft.options?.expiry ?? null } })}>+ Option leg</button>
          )}
          {draft.options && (
            <div className="flex flex-wrap items-end gap-space-3">
              <Field label="Strike">
                <select aria-label="Strike rule" value={draft.options.strike?.rule ?? ""} className={FIELD}
                  onChange={(e) => set({ options: { ...draft.options!, strike: e.target.value ? { rule: e.target.value as "atm" } : null } })}>
                  <option value="">Choose…</option><option value="atm">ATM</option><option value="itm">ITM by steps</option><option value="otm">OTM by steps</option>
                  <option value="delta">By delta</option><option value="premium">By premium</option>
                </select>
              </Field>
              {(draft.options.strike?.rule === "itm" || draft.options.strike?.rule === "otm") && <Field label="steps"><NumberBox label="Strike steps" step="1" min={0}
                value={draft.options.strike.steps} onChange={(steps) => set({ options: { ...draft.options!, strike: { ...draft.options!.strike!, steps } } })} /></Field>}
              {draft.options.strike?.rule === "delta" && <Field label="delta"><NumberBox label="Strike delta" value={draft.options.strike.delta}
                onChange={(delta) => set({ options: { ...draft.options!, strike: { ...draft.options!.strike!, delta } } })} /></Field>}
              {draft.options.strike?.rule === "premium" && <Field label="premium"><NumberBox label="Strike premium" value={draft.options.strike.premium}
                onChange={(premium) => set({ options: { ...draft.options!, strike: { ...draft.options!.strike!, premium } } })} /></Field>}
              <Field label="Expiry">
                <select aria-label="Expiry" value={draft.options.expiry?.rule ?? ""} className={FIELD}
                  onChange={(e) => set({ options: { ...draft.options!, expiry: e.target.value ? { rule: e.target.value as "current_week" } : null } })}>
                  <option value="">Choose…</option><option value="current_week">Current week</option><option value="next_week">Next week</option>
                  <option value="current_month">Current month</option><option value="next_month">Next month</option>
                </select>
              </Field>
            </div>
          )}
        </Section>
      </div>
    </Sheet>
  );
}

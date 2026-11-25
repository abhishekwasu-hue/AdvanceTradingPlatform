import { useEffect, useState, type FormEvent } from "react";
import { request } from "../api/client";
import { ApiError, errorSentence } from "../api/errors";
import { watchlistsV2Api, type WatchlistV2 } from "../chart/watchlistsV2";
import { SymbolPicker } from "../ui/SymbolPicker";
import { useFeatures } from "../ui/useFeatures";
import {
  appendInstruments, breakoutFieldErrors, breakoutInstrumentKey, instrumentKeys, type FieldErrors,
} from "./breakoutSettings";

const PROFILES = [
  ["BO-01", "Pure breakout"], ["BO-02", "RSI-confirmed breakout"],
  ["BO-03", "Breakout retest"], ["BO-04", "Momentum breakout"],
] as const;
const PRESETS = [
  ["Scalping", "1m", ["1m", "5m"], ["5m", "15m"]],
  ["Fast Intraday", "3m", ["5m"], ["15m", "1h"]],
  ["Intraday A", "5m", ["5m", "15m"], ["15m", "1h"]],
  ["Intraday B", "15m", ["15m"], ["1h"]],
  ["Positional", "30m", ["1h"], ["1h", "1d"]],
  ["Swing", "1h", ["1h"], ["1d"]],
] as const;
const TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "1d"];
const INPUT = "min-h-11 rounded-control border border-subtle bg-surface-1 px-space-3 text-fg";
type Rule = { profile: string; [key: string]: unknown };
type Prefs = { enabled: boolean; instruments: string[]; rules: Rule[] };
type Status = {
  worker_available: boolean; status?: string; last_scan_at?: string; timeframes?: string[]; sources?: string[];
  config_versions?: Record<string, string>; counts?: { processed: number; missing_data: number; failed: number };
};
type Response = { prefs: { amw_breakout: Prefs }; versions: { amw_breakout: number } };

function tfList(rule: Rule, key: string, fallback: string): string[] {
  const value = rule[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [fallback];
}

function FieldProblems({ id, errors }: { id: string; errors?: string[] }) {
  return errors?.length ? (
    <ul id={id} className="space-y-space-1 text-t12 text-warn" role="alert">
      {errors.map((error) => <li key={error}>{error}</li>)}
    </ul>
  ) : null;
}

export default function BreakoutSettingsV2() {
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [saved, setSaved] = useState<Prefs | null>(null);
  const [version, setVersion] = useState(0);
  const [instruments, setInstruments] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [watchlists, setWatchlists] = useState<WatchlistV2[]>([]);
  const [watchlistId, setWatchlistId] = useState("");
  const watchlistsEnabled = useFeatures().on("watchlists");
  useEffect(() => {
    let active = true;
    request<Response>("/me/prefs").then(({ prefs: data, versions }) => {
      if (!active) return;
      setPrefs(data.amw_breakout);
      setSaved(data.amw_breakout);
      setInstruments(data.amw_breakout.instruments.join(", "));
      setVersion(versions.amw_breakout);
    }).catch(() => {
      if (active) setMessage("Breakout settings could not be loaded.");
    });
    request<Status>("/me/amw-breakout/status").then((result) => {
      if (active) setStatus(result);
    }).catch(() => {});
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!watchlistsEnabled) return undefined;
    let active = true;
    void watchlistsV2Api.list().then((result) => {
      if (active) setWatchlists(result);
    }).catch(() => { if (active) setWatchlists([]); });
    return () => { active = false; };
  }, [watchlistsEnabled]);

  useEffect(() => {
    if (!prefs || !saved || (JSON.stringify(prefs) === JSON.stringify(saved) && instruments === saved.instruments.join(", "))) return;
    function warn(event: BeforeUnloadEvent) { event.preventDefault(); event.returnValue = ""; }
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [prefs, saved, instruments]);

  if (!prefs) return <p role="status">{message || "Loading breakout settings…"}</p>;
  const draft = prefs;
  const timeframes = TFS.filter((tf) => !status?.timeframes || status.timeframes.includes(tf));
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved) || instruments !== saved?.instruments.join(", ");
  const watchlist = watchlists.find((list) => String(list.id) === watchlistId);

  function change(rule: Rule, key: string, value: unknown) {
    setErrors((prev) => {
      const next = { ...prev };
      delete next[`rules.${draft.rules.findIndex((item) => item.profile === rule.profile)}.${key}`];
      return next;
    });
    setPrefs((prev) => prev && ({
      ...prev, rules: prev.rules.map((item) => item.profile === rule.profile ? { ...item, [key]: value } : item),
    }));
  }

  function addPicks(picks: Parameters<typeof appendInstruments>[1]) {
    const selected = appendInstruments(instrumentKeys(instruments), picks);
    setInstruments(selected.keys.join(", "));
    setErrors((prev) => ({ ...prev, instruments: [] }));
    const notes = [];
    if (selected.unsupported) notes.push(`${selected.unsupported} unsupported instrument(s) were skipped.`);
    if (selected.overLimit) notes.push("The scan list is limited to 10 instruments.");
    setMessage(notes.join(" "));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setErrors({});
    try {
      const result = await request<{ value: Prefs; version: number }>(`/me/prefs/amw_breakout?version=${version}`, {
        method: "PUT",
        body: JSON.stringify({
          ...draft, instruments: instrumentKeys(instruments),
        }),
      });
      setPrefs(result.value);
      setSaved(result.value);
      setInstruments(result.value.instruments.join(", "));
      setVersion(result.version);
      setMessage("Breakout settings saved.");
    } catch (error) {
      const fieldErrors = breakoutFieldErrors(error);
      setErrors(fieldErrors);
      if (Object.keys(fieldErrors).length) setMessage("Fix the marked fields before saving.");
      else if (error instanceof ApiError && error.status === 409) setMessage("These settings changed on another device. Reload this page before saving again.");
      else setMessage(errorSentence(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-space-4 text-t14">
      <dl className="grid gap-space-2 sm:grid-cols-2">
        <div><dt>Saved account opt-in</dt><dd>{saved?.enabled ? "Enabled" : "Disabled"}</dd></div>
        <div><dt>Worker heartbeat</dt><dd>{status ? status.worker_available ? "Available" : "Unavailable" : "Unknown"}</dd></div>
        {saved?.enabled && status?.status && <div><dt>Last scan status</dt><dd>{status.status.replace(/_/g, " ")}</dd></div>}
        {status?.last_scan_at && <div><dt>Last attempted scan</dt><dd>{new Date(status.last_scan_at).toLocaleString()}</dd></div>}
        {status?.counts && <div><dt>Profile scans with missing data</dt><dd>{status.counts.missing_data}</dd></div>}
      </dl>
      <fieldset disabled={saving} className="space-y-space-4">
        <label className="flex items-center gap-space-2">
          <input type="checkbox" checked={draft.enabled}
            onChange={(event) => setPrefs({ ...draft, enabled: event.target.checked })} />
          Enable informational breakout signals
        </label>
        <div className="grid gap-space-2 sm:grid-cols-2">
          <div className="min-w-0">
            <SymbolPicker value="" exchange="NSE" storageKey={null} label="Add NSE share or index"
              placeholder="Search NSE shares or indices" className="min-w-0 w-full"
              rowFilter={(item) => breakoutInstrumentKey(item) !== null} onPick={(item) => addPicks([item])} />
          </div>
          {watchlistsEnabled && watchlists.length > 0 && (
            <div className="flex min-w-0 flex-wrap items-end gap-space-2">
              <label className="grid min-w-0 flex-1 gap-space-1">
                Add from your watchlist
                <select className={INPUT} value={watchlistId} onChange={(event) => setWatchlistId(event.target.value)}>
                  <option value="">Choose a watchlist</option>
                  {watchlists.map((list) => <option key={list.id} value={list.id}>{list.name}</option>)}
                </select>
              </label>
              <button type="button" className={INPUT} disabled={!watchlist}
                onClick={() => watchlist && addPicks(watchlist.items)}>Add instruments</button>
            </div>
          )}
        </div>
        <label className="grid gap-space-1">
          NSE instrument keys, separated by commas
          <input className={INPUT} value={instruments} placeholder="NSE_INDEX:NIFTY50, NSE:RELIANCE"
            aria-invalid={Boolean(errors.instruments?.length)} aria-describedby={errors.instruments?.length ? "breakout-instruments-errors" : undefined}
            onChange={(event) => { setInstruments(event.target.value); setErrors({ ...errors, instruments: [] }); }} />
        </label>
        <FieldProblems id="breakout-instruments-errors" errors={errors.instruments} />
        <FieldProblems id="breakout-rules-errors" errors={errors.rules} />
        {PROFILES.map(([id, label]) => {
          const ruleIndex = draft.rules.findIndex((item) => item.profile === id);
          const rule = draft.rules[ruleIndex];
          const errorPrefix = `rules.${ruleIndex}`;
          const errorList = (key: string) => errors[`${errorPrefix}.${key}`];
          const errorId = (key: string) => `breakout-${id}-${key}-errors`;
          const fieldProps = (key: string) => ({
            "aria-invalid": Boolean(errorList(key)?.length),
            "aria-describedby": errorList(key)?.length ? errorId(key) : undefined,
          });
          return (
            <fieldset key={id} className="space-y-space-3 rounded-panel border border-subtle p-space-3">
              <legend className="px-space-2">{label}</legend>
              <label className="flex items-center gap-space-2">
                <input type="checkbox" checked={Boolean(rule)} onChange={(event) => setPrefs({
                  ...draft, rules: event.target.checked ? [...draft.rules, { profile: id }] :
                    draft.rules.filter((item) => item.profile !== id),
                })} />
                Use {label.toLowerCase()}
              </label>
              <FieldProblems id={errorId("profile")} errors={[...(errors[errorPrefix] ?? []), ...(errorList("profile") ?? [])]} />
              {rule && status?.config_versions?.[id] && <p className="text-fg-muted">
                Last scanned configuration {status.config_versions[id].slice(0, 12)}
              </p>}
              {rule && <div className="grid gap-space-3 sm:grid-cols-2">
                <label className="grid gap-space-1">
                  Apply a timeframe preset
                  <select className={INPUT} value="" onChange={(event) => {
                    const preset = PRESETS.find(([name]) => name === event.target.value);
                    if (!preset) return;
                    const [, tf, rsi, zones] = preset;
                    const sources = Array.isArray(rule.sources) ? rule.sources : ["B1", "B2"];
                    setPrefs({ ...draft, rules: draft.rules.map((item) => item.profile === id ?
                      { ...item, breakout_tf: tf, structure_tf: tf, confirmation_tf: null,
                        rsi_tfs: [...rsi], demand_tfs: [...zones], supply_tfs: [...zones], sources } : item) });
                  }}>
                    <option value="">Custom / choose a preset</option>
                    {PRESETS.map(([name]) => <option key={name}>{name}</option>)}
                  </select>
                </label>
                <button type="button" className={INPUT} onClick={() => setPrefs({
                  ...draft, rules: draft.rules.map((item) => item.profile === id ? { profile: id } : item),
                })}>Restore profile defaults</button>
                <label className="grid gap-space-1">
                  Direction
                  <select className={INPUT} value={String(rule.direction ?? "BOTH")} {...fieldProps("direction")}
                    onChange={(event) => change(rule, "direction", event.target.value)}>
                    <option value="BOTH">Bullish and bearish</option>
                    <option value="LONG_ONLY">Bullish only</option>
                    <option value="SHORT_ONLY">Bearish only</option>
                  </select>
                  <FieldProblems id={errorId("direction")} errors={errorList("direction")} />
                </label>
                {[["breakout_tf", "Breakout timeframe"], ["structure_tf", "Market structure timeframe"]].map(([key, title]) =>
                  <label className="grid gap-space-1" key={key}>
                    {title}
                    <select className={INPUT} value={String(rule[key] ?? "15m")} {...fieldProps(key)}
                      onChange={(event) => change(rule, key, event.target.value)}>
                      {timeframes.map((tf) => <option key={tf}>{tf}</option>)}
                    </select>
                    <FieldProblems id={errorId(key)} errors={errorList(key)} />
                  </label>)}
                {[
                  ...(id === "BO-01" ? [] : [["rsi_tfs", "RSI timeframes", "15m"]]),
                  ["demand_tfs", "Demand timeframes", "1h"], ["supply_tfs", "Supply timeframes", "1h"],
                  ["htf_tfs", "Higher-timeframe context", ""],
                ].map(([key, title, fallback]) => {
                  const selected = tfList(rule, key, fallback).filter(Boolean);
                  return <fieldset key={key} className="flex flex-wrap gap-space-2" {...fieldProps(key)}>
                    <legend>{title}</legend>
                    {timeframes.map((tf) => <label key={tf} className="flex min-h-11 items-center gap-space-1">
                      <input type="checkbox" checked={selected.includes(tf)}
                        onChange={(event) => change(rule, key, event.target.checked ? [...selected, tf] :
                          selected.filter((value) => value !== tf))} />
                      {tf}
                    </label>)}
                    <FieldProblems id={errorId(key)} errors={errorList(key)} />
                  </fieldset>;
                })}
                <label className="grid gap-space-1">
                  Confirmation mode
                  <select className={INPUT} value={String(rule.acceptance ?? (id === "BO-03" ? "RETEST_BREAKOUT" : "DIRECT_BREAKOUT"))}
                    disabled={id === "BO-03"} onChange={(event) => change(rule, "acceptance", event.target.value)}>
                    <option value="DIRECT_BREAKOUT">Direct breakout</option>
                    <option value="FOLLOW_THROUGH">Follow-through</option>
                    <option value="RETEST_BREAKOUT">Retest breakout</option>
                  </select>
                </label>
                <label className="grid gap-space-1">
                  Candle quality mode
                  <select className={INPUT} value={id === "BO-04" ? "MANDATORY" : String(rule.candle_mode ?? "QUALITY_FACTOR")}
                    disabled={id === "BO-04"} onChange={(event) => change(rule, "candle_mode", event.target.value)}>
                    <option value="OFF">Off</option>
                    <option value="QUALITY_FACTOR">Score contribution</option>
                    <option value="MANDATORY">Required to publish</option>
                  </select>
                </label>
                <label className="flex min-h-11 items-center gap-space-2">
                  <input type="checkbox" checked={rule.volume_mandatory === true}
                    onChange={(event) => change(rule, "volume_mandatory", event.target.checked)} />
                  Require reliable relative volume
                </label>
                {id !== "BO-01" && <label className="grid gap-space-1">
                  Divergence types
                  <select className={INPUT} value={String(rule.divergence_types ?? "BOTH_TYPES")}
                    onChange={(event) => change(rule, "divergence_types", event.target.value)}>
                    <option value="BOTH_TYPES">Regular and hidden</option>
                    <option value="REGULAR_ONLY">Regular only</option>
                    <option value="HIDDEN_ONLY">Hidden only</option>
                  </select>
                </label>}
                <fieldset className="flex flex-wrap gap-space-3" {...fieldProps("sources")}>
                  <legend>Breakout sources</legend>
                  {([["B1", "Range breakout"], ["B2", "Confirmed swing"], ["HORIZONTAL", "Horizontal level"], ["ZONE", "Zone boundary"], ["SESSION", "Previous session"]] as const).filter(([source]) => !status?.sources || status.sources.includes(source)).map(([source, title]) => {
                    const selected = Array.isArray(rule.sources) ? rule.sources as string[] : ["B1", "B2"];
                    return <label className="flex min-h-11 items-center gap-space-1" key={source}>
                      <input type="checkbox" checked={selected.includes(source)}
                        onChange={(event) => change(rule, "sources", event.target.checked ? [...selected, source] :
                          selected.filter((value) => value !== source))} />
                      {title}
                    </label>;
                  })}
                  <FieldProblems id={errorId("sources")} errors={errorList("sources")} />
                </fieldset>
                {([
                  ["buffer", "Breakout buffer (ATR)", 0.2, 0, 5, 0.01],
                  ["max_extension", "Maximum extension (ATR)", 1.5, 0, 20, 0.01],
                  ["zone_block", "Block zone clearance (ATR)", 0.5, 0, 20, 0.01],
                  ["zone_pass", "Pass above zone clearance (ATR)", 2, 0, 20, 0.01],
                  ["body_min", "Minimum candle body ratio", 0.6, 0, 1, 0.01],
                  ["close_max", "Maximum distance from directional close", 0.25, 0, 1, 0.01],
                  ["divergence_age", "Divergence age (source candles)", 15, 0, 200, 1],
                  ["retest_tolerance", "Retest tolerance (ATR)", 0.25, 0, 5, 0.01],
                  ["volume_reference", "Relative volume reference", 1.5, 0, 20, 0.01],
                  ["follow_bars", "Follow-through candles", 1, 1, 32, 1],
                  ["rsi_period", "RSI period", 14, 2, 200, 1],
                  ["swing_degree", "Swing degree", 1, 0, 3, 1],
                  ["max_age", "Pending setup age (candles)", 5, 0, 32, 1],
                  ["min_score", "Publication score preference (effective floor 70)", 70, 55, 100, 1],
                ] as const).map(([key, title, fallback, min, max, step]) =>
                  <label className="grid gap-space-1" key={key}>
                    {title}
                    <input className={INPUT} type="number" min={min} max={max} step={step} required {...fieldProps(key)}
                      value={rule[key] === "" ? "" : typeof rule[key] === "number" ? Number(rule[key]) : fallback}
                      onChange={(event) => change(rule, key, event.target.value === "" ? "" : event.target.valueAsNumber)} />
                    <FieldProblems id={errorId(key)} errors={errorList(key)} />
                  </label>)}
              </div>}
            </fieldset>
          );
        })}
      </fieldset>
      <button type="submit" className={INPUT} disabled={saving || !dirty}>{saving ? "Saving…" : "Save breakout settings"}</button>
      <button type="button" className={`${INPUT} ml-space-2`} disabled={!dirty || saving || !saved}
        onClick={() => {
          if (!saved) return;
          setPrefs(saved);
          setInstruments(saved.instruments.join(", "));
          setErrors({});
          setMessage("");
        }}>Cancel changes</button>
      {dirty && <p>Unsaved changes</p>}
      {message && <p role="status">{message}</p>}
    </form>
  );
}

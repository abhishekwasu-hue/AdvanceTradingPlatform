import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, Disclaimer } from "../components/ui";
import {
  defaultCondition,
  defaultCustomStrategyConfig,
  type Condition,
  type ConditionOperator,
  type CustomStrategyConfig,
  type CustomStrategyResponse,
  type IndicatorName,
  type Operand,
  type ParseStrategyResult,
} from "../types";

const INDICATORS: IndicatorName[] = ["EMA", "SMA", "RSI", "ADX", "PLUS_DI", "MINUS_DI", "ATR", "SUPERTREND", "CLOSE", "OPEN", "HIGH", "LOW"];
const PERIODLESS = new Set(["CLOSE", "OPEN", "HIGH", "LOW"]);
const OPERATORS: { value: ConditionOperator; label: string }[] = [
  { value: "GT", label: ">" },
  { value: "LT", label: "<" },
  { value: "GTE", label: ">=" },
  { value: "LTE", label: "<=" },
  { value: "CROSSES_ABOVE", label: "crosses above" },
  { value: "CROSSES_BELOW", label: "crosses below" },
];
const TIMEFRAMES = ["1min", "5min", "15min", "30min", "60min"];

export function OperandEditor({ operand, onChange }: { operand: Operand; onChange: (o: Operand) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <select
        className="rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
        value={operand.type}
        onChange={(e) => onChange({ ...operand, type: e.target.value as "value" | "indicator" })}
      >
        <option value="indicator">Indicator</option>
        <option value="value">Fixed value</option>
      </select>
      {operand.type === "value" ? (
        <input
          type="number"
          className="w-20 rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
          value={operand.value}
          onChange={(e) => onChange({ ...operand, value: Number(e.target.value) })}
        />
      ) : (
        <>
          <select
            className="rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
            value={operand.indicator}
            onChange={(e) => onChange({ ...operand, indicator: e.target.value as IndicatorName })}
          >
            {INDICATORS.map((ind) => (
              <option key={ind} value={ind}>{ind}</option>
            ))}
          </select>
          {!PERIODLESS.has(operand.indicator) && (
            <input
              type="number"
              title="period"
              className="w-14 rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
              value={operand.period}
              onChange={(e) => onChange({ ...operand, period: Number(e.target.value) })}
            />
          )}
          {operand.indicator === "SUPERTREND" && (
            <input
              type="number"
              step="0.1"
              title="ATR multiplier"
              className="w-14 rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
              value={operand.multiplier}
              onChange={(e) => onChange({ ...operand, multiplier: Number(e.target.value) })}
            />
          )}
        </>
      )}
    </div>
  );
}

export function ConditionEditor({
  condition, onChange, onRemove,
}: {
  condition: Condition;
  onChange: (c: Condition) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-panel2/40 px-2 py-2">
      <OperandEditor operand={condition.left} onChange={(left) => onChange({ ...condition, left })} />
      <select
        className="rounded bg-panel2 border border-border px-1.5 py-1 text-xs"
        value={condition.operator}
        onChange={(e) => onChange({ ...condition, operator: e.target.value as ConditionOperator })}
      >
        {OPERATORS.map((op) => (
          <option key={op.value} value={op.value}>{op.label}</option>
        ))}
      </select>
      <OperandEditor operand={condition.right} onChange={(right) => onChange({ ...condition, right })} />
      <button onClick={onRemove} className="ml-auto text-xs text-danger hover:underline">
        Remove
      </button>
    </div>
  );
}

export function ConditionListEditor({
  title, conditions, onChange,
}: {
  title: string;
  conditions: Condition[];
  onChange: (c: Condition[]) => void;
}) {
  return (
    <div>
      <div className="text-xs text-muted mb-1.5">{title} (all must hold - AND)</div>
      <div className="space-y-1.5">
        {conditions.map((c, i) => (
          <ConditionEditor
            key={i}
            condition={c}
            onChange={(next) => onChange(conditions.map((existing, j) => (j === i ? next : existing)))}
            onRemove={() => onChange(conditions.filter((_, j) => j !== i))}
          />
        ))}
      </div>
      <button
        onClick={() => onChange([...conditions, defaultCondition()])}
        className="mt-1.5 text-xs text-brand hover:underline"
      >
        + Add condition
      </button>
    </div>
  );
}

export default function StrategyBuilderPage() {
  const { user, loading: authLoading } = useAuth();
  const [saved, setSaved] = useState<CustomStrategyResponse[]>([]);
  const [config, setConfig] = useState<CustomStrategyConfig>(defaultCustomStrategyConfig());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [description, setDescription] = useState("");
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [parseResult, setParseResult] = useState<ParseStrategyResult | null>(null);

  function refresh() {
    api.listCustomStrategies().then(setSaved).catch((e) => setError(String(e)));
  }

  useEffect(() => {
    if (user) refresh();
  }, [user]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api.createCustomStrategy(config);
      setMessage(`Saved "${config.name}". It now appears everywhere strategies are listed (Signals, Backtesting).`);
      setConfig(defaultCustomStrategyConfig());
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    await api.deleteCustomStrategy(id);
    refresh();
  }

  async function handleParse() {
    setParsing(true);
    setParseError(null);
    try {
      const result = await api.parseStrategyDescription(description, config.name);
      setParseResult(result);
    } catch (e) {
      setParseError(String(e));
    } finally {
      setParsing(false);
    }
  }

  function handleLoadParsedIntoBuilder() {
    if (!parseResult) return;
    setConfig(parseResult.config);
    setMessage("Loaded into the builder below - review every condition before saving.");
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-extrabold text-purple-400">Strategy Builder</h1>
        <Card>
          <p className="text-sm text-muted">
            Log in from the Account tab to build and save your own no-code strategies. A saved
            strategy runs through the exact same signal/backtest engine as the inbuilt ones.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-purple-400">Strategy Builder</h1>
        <p className="text-sm font-semibold text-purple-400/60">
          Compose entry rules from indicators - no code. Every rule set produces a real signal
          through the same engine as the inbuilt strategies (score, entry/SL/targets, backtest).
        </p>
      </div>

      <Disclaimer kind="ai" />

      <Card title="Describe your strategy in plain English">
        <p className="text-xs text-muted mb-2">
          A deterministic, rule-based parser - not a call to an external AI (no AI-provider
          credentials are configured). It recognizes a fixed set of phrasings (e.g. "Buy when
          RSI(14) crosses above 60 and price is above EMA 50. Sell when RSI crosses below 40. Use
          1.5x ATR stop loss. Target risk reward of 2. Use the 15min timeframe.") and shows you
          exactly what it understood - and what it didn't - before anything is loaded into the
          builder. Nothing is saved until you review it below and click "Save strategy".
        </p>
        <textarea
          className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
          rows={4}
          placeholder="Buy when RSI(14) crosses above 60 and price is above EMA 50. Sell when RSI crosses below 40..."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        {parseError && <div className="mt-2 text-sm text-danger">{parseError}</div>}
        <button
          onClick={handleParse}
          disabled={parsing || !description.trim()}
          className="mt-2 rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
        >
          {parsing ? "Parsing…" : "Parse"}
        </button>

        {parseResult && (
          <div className="mt-3 space-y-2">
            {parseResult.interpreted.length > 0 && (
              <div>
                <div className="text-xs text-muted mb-1">Understood as:</div>
                <ul className="space-y-0.5">
                  {parseResult.interpreted.map((line, i) => (
                    <li key={i} className="text-xs text-accent">✓ {line}</li>
                  ))}
                </ul>
              </div>
            )}
            {parseResult.warnings.length > 0 && (
              <div>
                <div className="text-xs text-muted mb-1">Not understood (won't be included):</div>
                <ul className="space-y-0.5">
                  {parseResult.warnings.map((line, i) => (
                    <li key={i} className="text-xs text-warn">⚠ {line}</li>
                  ))}
                </ul>
              </div>
            )}
            <button
              onClick={handleLoadParsedIntoBuilder}
              disabled={parseResult.interpreted.length === 0}
              className="rounded border border-brand/40 text-brand hover:bg-brand/10 px-3 py-1 text-xs disabled:opacity-50"
            >
              Load into builder below
            </button>
          </div>
        )}
      </Card>

      <Card title={`Your saved strategies (${saved.length})`}>
        {saved.length === 0 ? (
          <div className="text-sm text-muted py-2">None yet - build one below.</div>
        ) : (
          <div className="space-y-1.5">
            {saved.map((s) => (
              <div key={s.id} className="flex items-center justify-between rounded border border-border px-3 py-2 text-sm">
                <div>
                  <span className="font-medium text-slate-200">{s.config.name}</span>{" "}
                  <span className="text-muted text-xs">({s.config.timeframe}, id {s.strategy_id})</span>
                </div>
                <button onClick={() => handleDelete(s.id)} className="text-xs text-danger hover:underline">
                  Delete
                </button>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Build a new strategy">
        <div className="space-y-4">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-muted mb-1">Name</label>
              <input
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={config.name}
                onChange={(e) => setConfig({ ...config, name: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Timeframe</label>
              <select
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={config.timeframe}
                onChange={(e) => setConfig({ ...config, timeframe: e.target.value })}
              >
                {TIMEFRAMES.map((tf) => (
                  <option key={tf} value={tf}>{tf}</option>
                ))}
              </select>
            </div>
          </div>

          <ConditionListEditor
            title="Long entry conditions"
            conditions={config.long_conditions}
            onChange={(long_conditions) => setConfig({ ...config, long_conditions })}
          />
          <ConditionListEditor
            title="Short entry conditions"
            conditions={config.short_conditions}
            onChange={(short_conditions) => setConfig({ ...config, short_conditions })}
          />

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-muted mb-1">Stop loss (x ATR)</label>
              <input
                type="number" step="0.1"
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={config.stop_loss_atr_mult}
                onChange={(e) => setConfig({ ...config, stop_loss_atr_mult: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">ATR period</label>
              <input
                type="number"
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={config.atr_period}
                onChange={(e) => setConfig({ ...config, atr_period: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Target R:R (1 / 2)</label>
              <div className="flex gap-1">
                <input
                  type="number" step="0.1"
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={config.target_rr[0]}
                  onChange={(e) => setConfig({ ...config, target_rr: [Number(e.target.value), config.target_rr[1]] })}
                />
                <input
                  type="number" step="0.1"
                  className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                  value={config.target_rr[1]}
                  onChange={(e) => setConfig({ ...config, target_rr: [config.target_rr[0], Number(e.target.value)] })}
                />
              </div>
            </div>
            <div>
              <label className="block text-xs text-muted mb-1">Min R:R to trade</label>
              <input
                type="number" step="0.1"
                className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
                value={config.min_rr}
                onChange={(e) => setConfig({ ...config, min_rr: Number(e.target.value) })}
              />
            </div>
          </div>

          {error && <div className="text-sm text-danger">{error}</div>}
          {message && <div className="text-sm text-accent">{message}</div>}

          <button
            onClick={handleSave}
            disabled={saving || (config.long_conditions.length === 0 && config.short_conditions.length === 0)}
            className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save strategy"}
          </button>
          {config.long_conditions.length === 0 && config.short_conditions.length === 0 && (
            <div className="text-xs text-muted">Add at least one long or short condition before saving.</div>
          )}
        </div>
      </Card>
    </div>
  );
}

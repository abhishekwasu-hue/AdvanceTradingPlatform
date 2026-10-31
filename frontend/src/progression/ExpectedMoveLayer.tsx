import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ChartEngine } from "../charting/engine";
import type { ExpectedMove } from "../types";
import { formatNumber, formatPrice } from "../ui/format";
import { CONE_LAYER_ID, coneLayer } from "./exposure";
import { useProgressionT } from "./i18n";

const HORIZONS = [1, 5, 10, 20];

/**
 * N2-4 (flag `decision_tools`): the expected-move cone on the chart - the ±1 sd and ±2 sd range around the last daily
 * close for the chosen number of sessions, from the instrument's historical volatility. Off until the user turns it on;
 * shown only when the organisation has the flag (`enabled`). A model of past moves, not a forecast.
 */
export default function ExpectedMoveLayer({ engine, instrument, enabled }: { engine: ChartEngine | null; instrument: string; enabled: boolean }) {
  const t = useProgressionT();
  const [on, setOn] = useState(false);
  const [horizon, setHorizon] = useState(5);
  const [move, setMove] = useState<ExpectedMove | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setMove(null); setFailed(false);
    if (!enabled || !on || !instrument) return () => { live = false; };
    api.decisionExpectedMove(instrument, horizon).then((m) => { if (live) setMove(m); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [enabled, on, instrument, horizon]);

  useEffect(() => {
    if (!engine) return;
    engine.setLayer(CONE_LAYER_ID, move ? coneLayer(move, t("decision.cone.band1", { sessions: move.selected.sessions }),
                                                    t("decision.cone.band2", { sessions: move.selected.sessions })) : null);
    return () => engine.setLayer(CONE_LAYER_ID, null);
  }, [engine, move, t]);

  if (!enabled) return null;
  return <ExpectedMoveControls on={on} horizon={horizon} move={move} failed={failed} onToggle={() => setOn(!on)} onHorizon={setHorizon} />;
}

export function ExpectedMoveControls({ on, horizon, move, failed, onToggle, onHorizon }: {
  on: boolean; horizon: number; move: ExpectedMove | null; failed: boolean; onToggle: () => void; onHorizon: (h: number) => void;
}) {
  const t = useProgressionT();
  const s = move?.selected;
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted" data-testid="expected-move">
      <button type="button" aria-pressed={on} onClick={onToggle}
              className={`min-h-[44px] rounded border px-2 py-1 sm:min-h-0 ${on ? "border-info text-info" : "border-border text-fg"}`}>
        {t("decision.cone.toggle")}
      </button>
      {on && (
        <label className="flex items-center gap-1">
          {t("decision.cone.horizon")}
          <select value={horizon} onChange={(e) => onHorizon(Number(e.target.value))}
                  className="min-h-[44px] rounded border border-border bg-surface-2 px-1 py-0.5 text-fg sm:min-h-0">
            {HORIZONS.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
        </label>
      )}
      {on && move && s && (
        <span title={move.note}>{t("decision.cone.legend", {
          instrument: move.instrument, close: formatPrice(move.close), date: move.close_date, sigma: `${formatNumber(move.sigma * 100, 1)}%`,
          method: move.method, sessions: s.sessions, low1: formatPrice(s.low_1sd), high1: formatPrice(s.high_1sd),
          low2: formatPrice(s.low_2sd), high2: formatPrice(s.high_2sd) })}</span>
      )}
      {on && failed && <span className="text-warn">{t("decision.cone.failed")}</span>}
    </div>
  );
}

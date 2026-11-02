import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { useAuth } from "../../auth/AuthContext";
import EmbeddedChartV2 from "../../chart/EmbeddedChartV2";
import { setOpenChart } from "../../chart/openChart";
import { ReadChartBody, useReadChart, type ReadState } from "../../chart/ReadChartV2";
import { DEFAULT_TIMEFRAME, type TimeframeId } from "../../chart/timeframes";
import type { LightweightEngine } from "../../charting/lightweight";
import { cx } from "../../components/primitives/cx";
import { useInPanel } from "../../inplace/inPanel";
import { SymbolPicker, readRecent, recentKeyFor, rememberSymbol, type PickedSymbol } from "../../ui/SymbolPicker";
import { useFeatures } from "../../ui/useFeatures";
import { AiCardV2, cardTimeframe, useWorkspaceCard } from "./AiCardV2";
import { workspaceLayer } from "./annotations";
import { AnnotationsV2 } from "./AnnotationsV2";
import { contractEmptyNote, isContract, openingSymbol, readingKey } from "./model";
import { PlanPanelV2 } from "./PlanPanelV2";
import { EventsV2 } from "./EventsV2";
import { PositionCardsV2 } from "./PositionCardsV2";
import { PartFillsV2 } from "./PartFillsV2";
import { WatchCardsV2 } from "./WatchCardsV2";
import { CloseSummaryV2 } from "./CloseSummaryV2";
import { SimilarSetupsV2 } from "./SimilarSetupsV2";
import { TeachV2 } from "./TeachV2";
import { Button, Sheet } from "../../components/primitives";
import { MyStudiesV2 } from "../../study/MyStudiesV2";
import { SaveStudySheetV2 } from "../../study/SaveStudySheetV2";

/**
 * M75 W-2 (owner order M75 CW-1, flag `copilot_workspace`): the chart workspace inside the Copilot's "Read the chart"
 * tab. Any index, share, future or option contract (the one SymbolPicker, with contracts) and any timeframe (the
 * chart's own timeframe dropdown); the chart is full width on a phone; opening a chart starts its reading by itself -
 * the existing read service (`GET /api/charts/read`, flag `read_chart`) as of the last closed bar, drawn on the chart as
 * its layers and written in the analysis panel beside it (below it on a phone). A new symbol or timeframe reads again.
 * Read-only: nothing here places, blocks or changes an order (ADR-0006), and no value is chosen for the trader - the
 * chart opens on his own last symbol, or asks for one.
 */
const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export default function ChartWorkspaceV2() {
  const { user } = useAuth();
  const features = useFeatures();
  const inPanel = useInPanel();
  const [params] = useSearchParams();
  const storageKey = recentKeyFor(user?.id ?? null);
  const [pick, setPick] = useState<PickedSymbol | null>(() => openingSymbol(params.get("symbol"), readRecent(storageKey)));
  const [tf, setTf] = useState<TimeframeId>(DEFAULT_TIMEFRAME);
  const [engine, setEngine] = useState<LightweightEngine | null>(null);
  const [attempt, setAttempt] = useState(0);
  const readOn = features.on("read_chart");
  const key = readingKey(pick, tf);
  // M75 W-3: the reading is drawn as its annotations (one source, labelled) when the server gives them
  const read = useReadChart(engine, key?.instrument ?? "", key?.tf ?? null, readOn && !!key, attempt, workspaceLayer);
  // M75 W-4: the AI card starts by itself once the code reading is there (the server runs it once per closed bar)
  // M76 MP-3 (M76Q-7): the open chart, for a check card opened beside it (cleared when the workspace closes)
  const openInstrument = key?.instrument ?? null;
  const openTf = key?.tf ?? null;
  useEffect(() => {
    setOpenChart(openInstrument && openTf ? { instrument: openInstrument, tf: openTf } : null);
    return () => setOpenChart(null);
  }, [openInstrument, openTf]);
  // M77 SX-7 (flag study_export): "Save study" and "My studies" open in place over the chart (same tab, same address)
  const studiesOn = features.on("study_export");
  const [saveOpen, setSaveOpen] = useState(false);
  const [studiesOpen, setStudiesOpen] = useState(false);
  const [saved, setSaved] = useState(0);
  const cardTf = cardTimeframe(key?.tf);
  const card = useWorkspaceCard(pick?.symbol ?? null, cardTf, read.status === "ready");
  // M76 MP-11 / MP-14: the reading's annotations for the watch cards, teach-on-chart and similar past setups
  const annotations = read.status === "ready" ? read.read.annotations ?? null : null;
  const choose = (next: PickedSymbol) => {
    if (!next.symbol.trim()) return;
    const clean = { symbol: next.symbol.trim().toUpperCase(), exchange: (next.exchange || "NSE").toUpperCase(), name: next.name };
    rememberSymbol(storageKey, clean);
    setPick(clean);
  };

  return (
    <section aria-labelledby="copilot-workspace-title" data-copilot-workspace={pick ? pick.symbol : "none"} className="space-y-space-3">
      <div className="flex flex-wrap items-center justify-between gap-space-2">
        <h2 id="copilot-workspace-title" className="text-t15 font-semibold text-fg">Chart</h2>
        {studiesOn && (
          <div className="flex flex-wrap gap-space-2" data-workspace-study>
            {pick && key && <Button size="sm" variant="primary" onClick={() => setSaveOpen(true)} data-open-save-study>Save study</Button>}
            <Button size="sm" variant="outline" onClick={() => setStudiesOpen(true)} data-open-my-studies>My studies</Button>
          </div>
        )}
      </div>
      <div className="grid gap-space-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        {/* Full width on a phone: the chart leaves the page's side padding (not inside a panel, which has its own). */}
        <div className={cx("min-w-0", !inPanel && "-mx-space-4 md:mx-0")} data-workspace-chart>
          {pick ? (
            <EmbeddedChartV2 symbol={pick.symbol} exchange={pick.exchange} timeframe={tf} onTimeframe={setTf} onSymbol={choose}
              symbolUniverse="with-contracts" onEngine={setEngine} emptyNote={isContract(pick.exchange) ? contractEmptyNote(pick.symbol) : undefined}
              className="h-[60vh] md:h-[480px]" />
          ) : (
            <div className="rounded-panel border border-subtle bg-surface-1 p-space-4" data-workspace-pick>
              <p className="mb-space-2 text-t14 text-fg">Pick a symbol or a futures / option contract to open its chart. Its reading starts by itself.</p>
              <SymbolPicker value="" exchange="NSE" onPick={choose} storageKey={storageKey} universe="with-contracts" label="Symbol"
                className="w-full" placeholder="Search a symbol or contract" />
            </div>
          )}
          {/* M76 MP-7 (S-3): expiries, the calendar's events and his own, on the chart's own column (information only) */}
          {pick && !isContract(pick.exchange) && <div className={cx("mt-space-2", !inPanel && "px-space-4 md:px-0")}><EventsV2 symbol={pick.symbol} /></div>}
          {/* M76 MP-14 (L-2): tap an annotation for its meaning; MP-11 (D-1): his watch cards, set up in place */}
          {pick && (
            <div className={cx("mt-space-2 space-y-space-2", !inPanel && "px-space-4 md:px-0")}>
              <TeachV2 engine={engine} annotations={annotations} />
              {!isContract(pick.exchange) && <WatchCardsV2 instrument={key?.instrument ?? null} tf={key?.tf ?? null} annotations={annotations} />}
            </div>
          )}
        </div>
        <aside aria-labelledby="copilot-workspace-reading" data-workspace-analysis={readOn ? read.status : "off"}
          className="min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-4 lg:self-start">
          <h3 id="copilot-workspace-reading" className="mb-space-2 text-t14 font-semibold text-fg">The chart&apos;s reading</h3>
          <Reading state={read} readOn={readOn} hasChart={!!pick} offered={!!key} onRetry={() => setAttempt((n) => n + 1)} />
          {read.status === "ready" && <div className="mt-space-3"><AiCardV2 state={card} offered={!!cardTf} /></div>}
          {/* M76 MP-14 (L-1): his own past trades at the same kind of area */}
          {annotations && <div className="mt-space-3 border-t border-subtle pt-space-3"><SimilarSetupsV2 annotations={annotations} /></div>}
        </aside>
      </div>
      {/* M75 W-6 (CW-4): the trade plan panel in the same tab, for the chart's symbol (preview only). */}
      {/* M76 MP-6 (S-2): the open chart's reading, for the plan's P&L at its next areas - only on the underlying's own chart
          (a futures / option contract's areas are that contract's prices, not the spot the plan is priced at) */}
      {/* M76 MP-10 (E-3 / E-4): his live positions on this underlying, the one-tap exits through the existing exit paths */}
      {/* M76 MP-9 (E-2): a spread that filled only in part - per leg, with each unfilled leg's new last price (his confirm) */}
      {pick && !isContract(pick.exchange) && <PartFillsV2 symbol={pick.symbol} />}
      {pick && !isContract(pick.exchange) && <PositionCardsV2 symbol={pick.symbol} engine={engine} />}
      {pick && <PlanPanelV2 symbol={pick.symbol}
        annotations={read.status === "ready" && !isContract(pick.exchange) ? read.read.annotations ?? null : null} />}
      {/* M76 MP-13 (D-3): his day in numbers, "Close summary" after the close */}
      <CloseSummaryV2 />
      {studiesOn && pick && key && (
        <SaveStudySheetV2 open={saveOpen} onOpenChange={setSaveOpen} instrument={key.instrument} symbol={pick.symbol} openTf={key.tf}
          onSaved={() => setSaved((n) => n + 1)} />
      )}
      {studiesOn && (
        <Sheet open={studiesOpen} onOpenChange={setStudiesOpen} title="My studies" wide>
          <MyStudiesV2 refresh={saved} />
        </Sheet>
      )}
    </section>
  );
}

/** The analysis panel's words for each state of the reading. */
export function Reading({ state, readOn, hasChart, offered, onRetry }: {
  state: ReadState; readOn: boolean; hasChart: boolean; offered: boolean; onRetry: () => void;
}) {
  const muted = "text-t13 text-fg-muted";
  if (!readOn) return <p className={muted}>The chart&apos;s reading is not available for your account yet.</p>;
  if (!hasChart) return <p className={muted}>Open a chart: its reading starts by itself.</p>;
  if (!offered) return <p className={muted}>The reading is offered on the minute, hour and Daily timeframes. Pick one of them.</p>;
  if (state.status === "loading" || state.status === "idle") return <p role="status" className={muted}>Reading this chart…</p>;
  if (state.status === "off") return <p className={muted}>The reading is not available for this chart.</p>;
  if (state.status === "failed") {
    return (
      <p role="status" className={cx(muted, "flex flex-wrap items-center gap-x-space-2")}>
        This chart could not be read.
        <button type="button" onClick={onRetry} data-workspace-retry className={cx("min-h-11 font-semibold text-brand hover:underline md:min-h-9", FOCUS)}>Try again</button>
      </p>
    );
  }
  if (state.status !== "ready") return null;
  const a = state.read.annotations;
  return a ? <div className="space-y-space-3"><AnnotationsV2 annotations={a} /><ReadChartBody read={state.read} /></div> : <ReadChartBody read={state.read} />;
}

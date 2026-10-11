import type { Meta, StoryObj } from "@storybook/react";
import { useState } from "react";
import { FreshnessPill } from "../screener/FreshnessPill";
import { FunnelCanvas } from "../screener/FunnelCanvas";
import { newBlock, newStage, runStamp, sameRun, stageText, type FunnelResult, type Registry, type RunStamp, type Stage } from "../screener/model";
import contract from "../screener/builderTexts.json";
import { ResultBoard } from "../screener/ResultBoard";
import { StageRow } from "../screener/StageRow";
import { SurvivorTrail } from "../screener/SurvivorTrail";

/** U5 D1 - the screener's funnel, its stages and the result table. Every story follows the toolbar's theme and
 * colour-blind setting. */
const meta: Meta = { title: "Screener/Funnel" };
export default meta;
type Story = StoryObj;

const e = (args: string[], types: Record<string, "num" | "window">, defaults: Record<string, number> = {}, unit = "price") => ({
  kind: "factor" as const, returns: "num" as const, unit, args, types, defaults, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "",
});
export const REGISTRY: Registry = {
  close: { ...e([], {}), field: true },
  volume: { ...e([], {}, {}, "volume"), field: true },
  RSI: e(["n"], { n: "window" }, { n: 14 }, "index"),
  EMA: e(["x", "n"], { x: "num", n: "window" }, {}, "same"),
  ATR: e(["n"], { n: "window" }, { n: 14 }),
  Supertrend: e(["n", "k"], { n: "window", k: "num" }, { n: 10, k: 3 }),
};

function stages(): Stage[] {
  const a = newStage(REGISTRY, "RSI", { kind: "value", value: 60 });
  a.left.tf = "15m";
  const b = newStage(REGISTRY, "close", { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 20 }, tf: null } });
  const c = newStage(REGISTRY, "volume", { kind: "value", value: 500000 });
  return [a, b, c];
}

const SYMBOLS = ["RELIANCE", "TCS", "INFY"];

function funnelFor(list: Stage[], survivors: number[], withData = 48): { funnel: FunnelResult; ran: RunStamp } {
  const enabled = list.filter((s) => s.enabled);
  return {
    funnel: { universe: 50, with_data: withData, stages: enabled.map((s, i) => ({ text: stageText(REGISTRY, s), survivors: survivors[i], removed: i === 2 ? ["ABB", "TCS"] : [] })) },
    ran: runStamp(REGISTRY, list, "5m", SYMBOLS),
  };
}
const fresh = (ran: RunStamp, list: Stage[]) => sameRun(ran, runStamp(REGISTRY, list, "5m", SYMBOLS));

export const Empty: Story = {
  render: function Empty() {
    const [list, setList] = useState<Stage[]>([]);
    return <FunnelCanvas stages={list} registry={REGISTRY} scanTf="5m" universe={50} funnel={null} fresh={false} matched={null} problems={{}} onChange={setList} />;
  },
};

export const Editing: Story = {
  render: function Editing() {
    const [list, setList] = useState<Stage[]>(stages);
    return <FunnelCanvas stages={list} registry={REGISTRY} scanTf="5m" universe={50} funnel={null} fresh={false} matched={null} problems={{}} onChange={setList} />;
  },
};

export const AfterARun: Story = {
  render: function AfterARun() {
    const [list, setList] = useState<Stage[]>(stages);
    const [{ funnel, ran }] = useState(() => funnelFor(list, [21, 9, 4]));
    return <FunnelCanvas stages={list} registry={REGISTRY} scanTf="5m" universe={50} funnel={funnel} fresh={fresh(ran, list)} matched={4} problems={{}} runKey={1} onChange={setList} />;
  },
};

export const AStageRemovesEverything: Story = {
  render: function Kills() {
    const [list, setList] = useState<Stage[]>(stages);
    const [{ funnel, ran }] = useState(() => funnelFor(list, [21, 9, 0]));
    return <FunnelCanvas stages={list} registry={REGISTRY} scanTf="5m" universe={50} funnel={funnel} fresh={fresh(ran, list)} matched={0} problems={{}} onChange={setList} />;
  },
};

export const AProblemOnAStage: Story = {
  render: function Problem() {
    const [list, setList] = useState<Stage[]>(stages);
    return <FunnelCanvas stages={list} registry={REGISTRY} scanTf="5m" universe={50} funnel={null} fresh={false} matched={null}
                         problems={{ [list[1].id]: "close cannot be compared with RSI(14): a price against an oscillator. Compare it with another price." }} onChange={setList} />;
  },
};

export const OneStage: Story = {
  render: function One() {
    const [s, setS] = useState<Stage>(() => stages()[0]);
    return (
      <ol className="max-w-3xl rounded-control border border-border bg-surface">
        <StageRow stage={s} index={0} registry={REGISTRY} scanTf="5m" trail={{ id: s.id, survivors: 12, removed: 36, kills: false }} total={48}
                  onChange={setS} onMove={() => undefined} onDuplicate={() => undefined} onRemove={() => undefined} />
      </ol>
    );
  },
};

export const Trails: Story = {
  render: () => (
    <div className="flex gap-8">
      <SurvivorTrail survivors={null} removed={null} total={null} />
      <SurvivorTrail survivors={31} removed={17} total={48} />
      <SurvivorTrail survivors={0} removed={4} total={48} kills onShowRemoved={() => undefined} />
    </div>
  ),
};

export const Results: Story = {
  render: () => (
    <div className="max-w-md">
      <ResultBoard runKey={1} results={[
        { symbol: "RELIANCE", matched: true, reason: null }, { symbol: "TCS", matched: false, reason: null },
        { symbol: "INFY", matched: true, reason: null }, { symbol: "ABB", matched: false, reason: "no bars from the broker" },
      ]} />
    </div>
  ),
};

export const ResultsAfterAnEdit: Story = {
  render: () => (
    <div className="max-w-md">
      <ResultBoard runKey={1} stale results={[{ symbol: "RELIANCE", matched: true, reason: null }, { symbol: "TCS", matched: false, reason: null }]} />
    </div>
  ),
};

export const Freshness: Story = {
  render: () => {
    const now = new Date();
    return (
      <div className="flex flex-wrap gap-3">
        <FreshnessPill ranAt={null} source={null} scanTf="5m" />
        <FreshnessPill ranAt={new Date(now.getTime() - 3 * 60e3)} source="broker:upstox" scanTf="5m" now={now} />
        <FreshnessPill ranAt={new Date(now.getTime() - 14 * 60e3)} source="broker:upstox" scanTf="5m" now={now} />
      </div>
    );
  },
};

/** D2: every block form in one funnel - an indicator, a filter, a category, a rank and an ANY group. */
const FULL = { ...REGISTRY, ...(contract.registry as unknown as Registry) };

export const BlockForms: Story = {
  render: function BlockForms() {
    const [list, setList] = useState<Stage[]>(() => [
      newStage(FULL, "RSI"), newBlock(FULL, "filter", "Pattern"), newBlock(FULL, "category", "Trend"), newBlock(FULL, "rank", "PctChange"),
      newBlock(FULL, "group"),
    ]);
    return <FunnelCanvas stages={list} registry={FULL} scanTf="5m" universe={50} funnel={null} fresh={false} matched={null} problems={{}} onChange={setList} />;
  },
};

export const LiveCounts: Story = {
  render: function LiveCounts() {
    const [list, setList] = useState<Stage[]>(stages);
    const [{ funnel, ran }] = useState(() => funnelFor(list, [21, 9, 4]));
    return <FunnelCanvas stages={list} registry={REGISTRY} scanTf="5m" universe={50} funnel={funnel} fresh={fresh(ran, list)} live matched={4} problems={{}} onChange={setList} />;
  },
};

export const WhyMatched: Story = {
  render: () => (
    <div className="max-w-md">
      <ResultBoard runKey={1} results={[{ symbol: "RELIANCE", matched: true, reason: null }, { symbol: "TCS", matched: false, reason: null }]}
                   why={(s) => [{ id: "a", summary: "RSI(14) on 15m above 60", passed: true }, { id: "b", summary: "close above EMA(close, 20)", passed: s === "RELIANCE" }]} />
    </div>
  ),
};

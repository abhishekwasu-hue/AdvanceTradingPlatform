import type { Meta, StoryObj } from "@storybook/react";
import "../copilot/copilot.css";
import AICore3D from "../copilot/components/AICore3D";
import AICoreFallback from "../copilot/components/AICoreFallback";
import AiProgress from "../copilot/components/AiProgress";
import BilingualQuestion from "../copilot/components/BilingualQuestion";
import CostChip from "../copilot/components/CostChip";
import RegimeDial from "../copilot/components/RegimeDial";
import SentimentGauge3D from "../copilot/components/SentimentGauge3D";
import SourceChips from "../copilot/components/SourceChips";
import TiltCard from "../copilot/components/TiltCard";
import { INTERVIEW_MR } from "../i18n/interviewSecondary";

/** The Copilot redesign's building blocks. Use the toolbar for dark / light and the colour-blind option. */
const meta: Meta = { title: "Copilot/Components" };
export default meta;
type Story = StoryObj;

export const AICore3DOrb: Story = {
  name: "AICore3D - orb (WebGL)",
  render: () => (
    <div className="grid grid-cols-3 gap-6">
      {(["bullish", "neutral", "bearish"] as const).map((tone) => (
        <div key={tone} className="text-center text-xs text-fg-muted"><AICore3D tone={tone} className="mx-auto h-48 w-48" />{tone}</div>
      ))}
    </div>
  ),
};

export const AICore3DParticles: Story = {
  name: "AICore3D - particle sphere (WebGL)",
  render: () => <AICore3D tone="neutral" variant="particles" className="h-64 w-64" />,
};

export const AICoreFallbackSvg: Story = {
  name: "AICore fallback (no WebGL / reduced motion)",
  render: () => (
    <div className="flex gap-6">
      {(["bullish", "neutral", "bearish"] as const).map((tone) => <AICoreFallback key={tone} tone={tone} className="h-40 w-40" />)}
    </div>
  ),
};

export const Tilt: Story = {
  name: "TiltCard",
  render: () => (
    <div className="grid max-w-3xl gap-4 md:grid-cols-3">
      <TiltCard><div className="text-sm font-semibold text-fg">AI glow</div><p className="text-xs text-fg-muted">Move the pointer over the card: it tilts up to 6°.</p></TiltCard>
      <TiltCard glow="up"><div className="text-sm font-semibold text-fg">Up glow</div><p className="text-xs text-fg-muted">For a risk-on read.</p></TiltCard>
      <TiltCard depth glow="none"><div className="text-sm font-semibold text-fg">Depth</div><p className="text-xs text-fg-muted">The backtest card floats higher.</p></TiltCard>
    </div>
  ),
};

export const Gauge: Story = {
  name: "SentimentGauge3D",
  render: () => (
    <div className="grid max-w-3xl grid-cols-2 gap-6 md:grid-cols-4">
      <SentimentGauge3D score={62} label="RISK_ON" coverage={0.8} />
      <SentimentGauge3D score={4} label="NEUTRAL" coverage={0.6} />
      <SentimentGauge3D score={-48} label="RISK_OFF" coverage={1} />
      <SentimentGauge3D score={null} label="UNKNOWN" />
    </div>
  ),
};

export const Dial: Story = {
  name: "RegimeDial",
  render: () => (
    <div className="grid max-w-3xl grid-cols-2 gap-6 md:grid-cols-5">
      {(["TREND_UP", "RANGE", "VOLATILE", "TREND_DOWN", "UNKNOWN"] as const).map((k) => <RegimeDial key={k} kind={k} detail="Higher timeframe: trending up" />)}
    </div>
  ),
};

export const Sources: Story = {
  name: "SourceChips",
  render: () => <SourceChips items={[{ kind: "brief" }, { kind: "memory" }, { kind: "concept", label: "ATR" }, { kind: "rules" }]} />,
};

export const Cost: Story = {
  name: "CostChip",
  render: () => <div className="flex gap-2"><CostChip tokens={1840} inr={0.42} /><CostChip tokens={10000} inr={4.2} estimate /></div>,
};

export const Bilingual: Story = {
  name: "BilingualQuestion",
  render: () => (
    <div className="max-w-xl">
      <BilingualQuestion en="A few questions first - capital, risk, style, time and goal." secondary={INTERVIEW_MR.startIntro} lang="mr"
        hint="Then the market data is read and three templates are tested on it." hintSecondary={INTERVIEW_MR.thanks}>
        <div className="flex gap-1.5">
          {["₹50,000", "₹1,00,000"].map((c) => <button key={c} className="rounded-full border border-border px-3 py-1.5 text-sm text-fg">{c}</button>)}
        </div>
      </BilingualQuestion>
      <p className="mt-3 text-xs text-fg-muted">Second line from the interview namespace: <span lang="mr">{INTERVIEW_MR.start}</span></p>
    </div>
  ),
};

export const Progress: Story = {
  name: "AiProgress",
  render: () => (
    <div className="max-w-md space-y-3">
      <AiProgress state={{ phase: "running", step: 1, steps: ["Reading market data…", "Writing rules…", "Running backtest…"], error: null, startedAt: 0 }} onCancel={() => undefined} />
      <AiProgress state={{ phase: "error", step: 0, steps: [], error: "This took too long and was stopped. Try again in a minute.", startedAt: 0 }} onCancel={() => undefined} onRetry={() => undefined} />
    </div>
  ),
};

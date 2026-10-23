import type { Meta, StoryObj } from "@storybook/react";
import { useState, type ReactNode } from "react";
import { Button } from "../components/primitives";
import { Chip, FilterChip, type ChipTone } from "../ui/Chip";
import { RangeBar } from "../ui/RangeBar";
import { StatusChip, type Status } from "../ui/StatusChip";

/**
 * Chart + colour order, Part 3 (docs/design/CHART_COLOUR_REVIEW.md, PR sequence item 8): the shared components that
 * carry colour by meaning. The brand accent stays ATP blue: ONE solid brand button per screen, everything else outlined
 * or ghost. Switch the theme and the colour-blind option from the toolbar.
 */
function Row({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="space-y-space-2">
      <h2 className="text-t15 font-semibold text-fg">{title}</h2>
      {note && <p className="text-t12 text-fg-muted">{note}</p>}
      <div className="flex flex-wrap items-center gap-space-3">{children}</div>
    </section>
  );
}

const TONES: ChipTone[] = ["neutral", "brand", "up", "down", "warn", "info", "ai"];
const STATUSES: Status[] = ["connected", "needs-action", "error", "info", "paper", "live"];

function FilterRow() {
  const [on, setOn] = useState("All");
  return <>{["All", "Waiting", "Taken", "Missed"].map((f) => <FilterChip key={f} selected={on === f} onClick={() => setOn(f)}>{f}</FilterChip>)}</>;
}

function ColourComponents() {
  return (
    <div className="max-w-3xl space-y-space-8">
      <Row title="Buttons" note="Primary is the screen's one main action (Place order, Start, Approve, Connect). Every other action is outline or ghost.">
        <Button variant="primary" size="v2">Connect</Button>
        <Button variant="outline" size="v2">Not now</Button>
        <Button variant="ghost" size="v2">What is this?</Button>
        <Button variant="danger" size="v2">Stop the bot</Button>
      </Row>
      <Row title="Chips - tint" note="Colour carries meaning only. AI violet only on AI entry points.">
        {TONES.map((t) => <Chip key={t} tone={t}>{t}</Chip>)}
      </Row>
      <Row title="Chips - outline">
        {TONES.map((t) => <Chip key={t} tone={t} variant="outline">{t}</Chip>)}
        <Chip variant="solid">Live</Chip>
      </Row>
      <Row title="Chips - badge shape (the P1.2 Badge)">
        {TONES.map((t) => <Chip key={t} tone={t} shape="badge">{t}</Chip>)}
      </Row>
      <Row title="Status chips" note="Connected is neutral with a check. Needs action is amber. An error is red only when money or an order is at stake. Paper is the neutral outline chip.">
        {STATUSES.map((s) => <StatusChip key={s} status={s} />)}
        <StatusChip status="error" atStake>Order not placed</StatusChip>
      </Row>
      <Row title="Filter chips" note="Selected: the brand outline and a light tint. Others: outlined.">
        <FilterRow />
      </Row>
      <section className="grid max-w-xl gap-space-4 rounded-panel border border-subtle bg-surface-1 p-space-4">
        <h2 className="text-t15 font-semibold text-fg">Range bars</h2>
        <RangeBar label="52-week range" low={18_200} high={26_300} value={24_812.35} />
        <RangeBar label="Today's range" low={24_650} high={24_910} value={24_890} />
        <RangeBar label="IV range" low={11.2} high={19.8} value={12.4} format={(v) => `${v.toFixed(1)} %`} />
        <RangeBar label="Day range" low={null} high={null} />
      </section>
    </div>
  );
}

const meta: Meta<typeof ColourComponents> = { title: "Design system/Colour by meaning", component: ColourComponents };
export default meta;
export const All: StoryObj<typeof ColourComponents> = {};

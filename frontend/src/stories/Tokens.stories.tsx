import type { Meta, StoryObj } from "@storybook/react";
import type { ReactNode } from "react";
import { formatChange, formatDateTimeIST, formatINR, formatLots, formatPct, formatPrice, formatSignedINR } from "../ui/format";

const SURFACES: [string, string][] = [
  ["surface", "Page background"], ["surface-1", "Cards, sidebar, top bar"], ["surface-2", "Read-only inputs, chips, hover"],
  ["surface-3", "Pressed"], ["border", "Inputs, buttons"], ["border-subtle", "v2: card outlines, dividers, rows"],
  ["warn-tint", "v2: behind a warning note"],
];
const MEANING: [string, string][] = [
  ["brand", "Only primary buttons, links, focus ring, current nav item, selected tab"],
  ["up", "Only rising price, profit, Long, target"],
  ["down", "Only falling price, loss, Short, stop"],
  ["warn", "Only real warnings: login needed, broker error, near a limit"],
  ["info", "Not used on v2 trader screens"],
];
// UI2-0 (UI v2 §8): the type roles, with the class each one uses.
const TYPE: [string, string, string][] = [
  ["text-t24 font-semibold", "Page title", "t24 · 24/30 · 600"],
  ["text-t20 font-semibold", "Page title on a phone", "t20 · 20/26 · 600 (new)"],
  ["text-t18 font-semibold", "Gate and dialog title", "t18 · 18/24 · 600"],
  ["text-t15 font-semibold", "Section (card) title, sentence case", "t15 · 15/20 · 600"],
  ["text-t14", "Body text - the default size on v2 screens.", "t14 · 14/20 · 400 (new)"],
  ["text-t12 font-medium text-fg-muted", "Caption and label", "t12 · 12/16 · 500, fg-muted"],
];
const SPACE = [1, 2, 3, 4, 5, 6, 8, 10, 12];
const SPACE_USE: Record<number, string> = {
  1: "icon to text in a chip", 2: "between buttons", 3: "inside list rows", 4: "phone gutter, card padding (phone)",
  5: "card padding (desktop)", 6: "gap between cards (desktop)", 8: "desktop gutter, between sections",
  10: "page bottom padding", 12: "empty-state padding",
};
// The formatter's own output for a few inputs (a catalogue of the rules, not data).
const NUMBERS: [string, string][] = [
  ["formatINR(124500)", formatINR(124500)],
  ["formatINR(24000000, compact)", formatINR(24000000, { compact: true })],
  ["formatSignedINR(3120)", formatSignedINR(3120)],
  ["formatSignedINR(-700)", formatSignedINR(-700)],
  ["formatPrice(24812.35, 0.05)", formatPrice(24812.35, 0.05)],
  ["formatChange(-8.2)", formatChange(-8.2)],
  ["formatPct(0.48)", formatPct(0.48)],
  ["formatLots(150, 75)", formatLots(150, 75)],
  ["formatDateTimeIST(05:00Z)", formatDateTimeIST("2026-10-08T05:00:00Z")],
];

function Swatch({ token, use, tall = false }: { token: string; use: string; tall?: boolean }) {
  return (
    <div className="w-40 rounded-panel border border-subtle bg-surface-1 p-2 text-t12">
      <div className={`mb-2 ${tall ? "h-12" : "h-8"} rounded-control border border-subtle`} style={{ background: `rgb(var(--${token}))` }} />
      <div className="font-semibold text-fg">{token}</div>
      <div className="text-fg-muted">{use}</div>
    </div>
  );
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section>
      <h2 className="text-t15 font-semibold text-fg">{title}</h2>
      {note && <p className="text-t12 text-fg-muted">{note}</p>}
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Tokens() {
  return (
    <div className="space-y-space-8">
      <Section title="Surfaces and outlines">
        <div className="flex flex-wrap gap-3">{SURFACES.map(([s, use]) => <Swatch key={s} token={s} use={use} tall />)}</div>
      </Section>

      <Section title="Colour carries meaning only" note="A successful state is neutral with a check, never green. Error chips are amber with an icon and a reason.">
        <div className="flex flex-wrap gap-3">{MEANING.map(([t, use]) => <Swatch key={t} token={t} use={use} />)}</div>
      </Section>

      <Section title="Type - Inter, tabular numbers" note="UI v2 adds t14 (body) and t20 (phone page title) to the t12 / t13 / t15 / t18 / t24 / t32 scale.">
        <div className="max-w-2xl divide-y divide-subtle rounded-panel border border-subtle bg-surface-1">
          {TYPE.map(([cls, sample, spec]) => (
            <div key={spec} className="flex flex-wrap items-baseline justify-between gap-2 px-space-4 py-space-3">
              <span className={cls.includes("text-fg-muted") ? cls : `${cls} text-fg`}>{sample}</span>
              <code className="text-t12 text-fg-muted">{spec}</code>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Numbers - one formatter (src/ui/format.ts)" note="Indian grouping, a true minus sign, times in IST; right-aligned and tabular in tables.">
        <div className="grid max-w-xl grid-cols-[1fr_auto] gap-x-space-6 gap-y-space-1 rounded-panel border border-subtle bg-surface-1 px-space-4 py-space-3 text-t14">
          {NUMBERS.map(([call, value]) => (
            <div key={call} className="contents">
              <code className="text-t12 text-fg-muted">{call}</code>
              <span className={`text-right font-tabular ${value.startsWith("+") ? "text-up" : value.startsWith("−") ? "text-down" : "text-fg"}`}>{value}</span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Spacing - the 4-pt grid" note="--space-1 to --space-12; in Tailwind p-space-4, gap-space-6 and so on.">
        <div className="space-y-1">
          {SPACE.map((n) => (
            <div key={n} className="flex items-center gap-3 text-t12">
              <code className="w-20 text-fg-muted">space-{n}</code>
              <div className="h-3 rounded-sm bg-brand" style={{ width: `var(--space-${n})` }} />
              <span className="text-fg-muted">{n * 4}px · {SPACE_USE[n]}</span>
            </div>
          ))}
        </div>
      </Section>

      <Section title="Radius, shadow and motion">
        <div className="flex flex-wrap items-start gap-space-6 text-t12">
          <div className="flex h-24 w-40 items-center justify-center rounded-control border border-border bg-surface-1 text-fg-muted">rounded-control 6</div>
          <div className="flex h-24 w-40 items-center justify-center rounded-panel border border-subtle bg-surface-1 text-center text-fg-muted shadow-card">rounded-panel 10<br />shadow-card</div>
          <div className="flex h-24 w-40 items-center justify-center rounded-panel border border-subtle bg-surface-1 text-center text-fg-muted shadow-raised">shadow-raised<br />menus, sheets</div>
          <div className="flex h-24 items-center gap-2">
            <span className="rounded-pill border border-subtle bg-surface-2 px-3 py-1 text-fg">rounded-pill</span>
            <span className="rounded-pill bg-warn-tint px-3 py-1 font-medium text-warn">Login needed</span>
          </div>
        </div>
        <p className="mt-space-3 text-t12 text-fg-muted">
          Motion: --dur-fast 120 ms (hover, press), --dur-base 200 ms (drawers, sheets), --ease cubic-bezier(0.2, 0, 0, 1). Both durations are 0 ms when motion is reduced.
        </p>
      </Section>
    </div>
  );
}

const meta: Meta<typeof Tokens> = { title: "Design system/Tokens", component: Tokens };
export default meta;
export const All: StoryObj<typeof Tokens> = {};

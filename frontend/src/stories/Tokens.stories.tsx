import type { Meta, StoryObj } from "@storybook/react";

const SURFACES = ["surface", "surface-1", "surface-2", "surface-3", "border"];
const MEANING = [["brand", "Links, primary action, focus"], ["up", "Profit / bullish"], ["down", "Loss / bearish"], ["warn", "Attention"], ["info", "Neutral information"]];

function Tokens() {
  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">Surfaces</h2>
        <div className="flex flex-wrap gap-3">
          {SURFACES.map((s) => (
            <div key={s} className="w-32 rounded-lg border border-border p-2 text-xs text-fg-muted">
              <div className="mb-2 h-12 rounded" style={{ background: `rgb(var(--${s}))` }} />{s}
            </div>
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">Colour carries meaning only</h2>
        <div className="flex flex-wrap gap-3">
          {MEANING.map(([t, use]) => (
            <div key={t} className="w-40 rounded-lg border border-border p-2 text-xs">
              <div className="mb-2 h-8 rounded" style={{ background: `rgb(var(--${t}))` }} />
              <div className="font-semibold text-fg">{t}</div><div className="text-fg-muted">{use}</div>
            </div>
          ))}
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-sm font-semibold text-fg">Typography - Inter, tabular numbers</h2>
        <p className="text-xl font-semibold text-fg">Page title 20/semibold</p>
        <p className="text-sm text-fg">Body 14/regular - the default text size.</p>
        <p className="text-xs text-fg-muted">Caption 12/muted</p>
        <p className="mt-2 font-tabular text-sm text-fg">24,512.35<br />  1,250.00<br />    -830.50</p>
      </section>
    </div>
  );
}

const meta: Meta<typeof Tokens> = { title: "Design system/Tokens", component: Tokens };
export default meta;
export const All: StoryObj<typeof Tokens> = {};

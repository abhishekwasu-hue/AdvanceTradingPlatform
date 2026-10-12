import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import builderTexts from "./builderTexts.json";
import { FunnelCanvas } from "./FunnelCanvas";
import { blockProblems, moveStage, newStage, screenText, stageSpans, trail, type Registry, type Stage } from "./model";

/** U5 D5: the spec's 50-stage canvas. The work a keystroke or a drag triggers (the text, the spans, the trail, the
 * problems, a move) and a full render of the canvas stay well inside one frame budget's order (a loose bound so a
 * slow CI box does not flake; the point is to catch an accidental quadratic-or-worse path). */
const registry = (builderTexts as unknown as { registry: Registry }).registry;
const fifty: Stage[] = Array.from({ length: 50 }, (_, i) => newStage(registry, i % 2 ? "RSI" : "close", { kind: "value", value: 40 + (i % 30) }));
const funnel = { universe: 50, with_data: 50, stages: fifty.map((_, i) => ({ text: `s${i}`, survivors: 50 - i })) };

function best(fn: () => void, runs = 5): number {
  let t = Infinity;
  for (let i = 0; i < runs; i++) {
    const t0 = performance.now();
    fn();
    t = Math.min(t, performance.now() - t0);
  }
  return t;
}

describe("50 stages (U5 D5)", () => {
  it("keeps the per-edit model work and a full canvas render fast", () => {
    const edit = best(() => {
      const text = screenText(registry, fifty);
      stageSpans(registry, fifty);
      trail(fifty, funnel, true);
      blockProblems(registry, fifty);
      moveStage(fifty, fifty[25].id, 1);
      expect(text.length).toBeGreaterThan(0);
    });
    const render = best(() => {
      renderToStaticMarkup(<FunnelCanvas stages={fifty} registry={registry} scanTf="1d" universe={50} funnel={funnel} fresh matched={1} problems={{}} onChange={() => undefined} />);
    }, 3);
    expect(edit).toBeLessThan(50);                 // ms, measured ~1-3 ms locally
    expect(render).toBeLessThan(400);              // ms, a static render of all 50 rows
  });
});

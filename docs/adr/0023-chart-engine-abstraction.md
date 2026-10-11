# ADR-0023: One ChartEngine interface over swappable chart engines; platform layers and drawings live above it

**Date:** 2026-10-11 · **Status:** provisional (spec 0.6: the recommended option, until the owner says otherwise) · **Part:** CH0

## Context
Charts are ProChart on lightweight-charts 4: 9 indicators and no drawing tools. The charting spec wants:
- drawings with storage;
- 100+ studies;
- chart types, multi-chart layouts and replay;
- the platform's own layers (price action, options, trades, screener);
- option and spread charts.

Two routes exist:
- **TradingView Advanced Charts:** proprietary, licensed per company on application, logo required. It provides most
  of the generic features.
- **Open-source lightweight-charts v5 primitives:** Apache-2.0, attribution link. We would build the drawings and
  features ourselves; KLineChart is the fallback.

Access to the first is not under our control. The second costs build time.

## Options
1. Pick one engine and code every page against it.
2. One engine-agnostic interface (`ChartEngine`), with the platform value (layers, drawings, actions, layouts) above it
   and each engine as an adapter below it.

## Decision (provisional) - option 2
- **The interface.** `frontend/src/charting/engine.ts` covers symbol/timeframe, bars (set/append), studies, drawings
  (add/update/remove), serialize/deserialize, `setLayer` for platform overlays, and events (crosshair, click,
  drawingChanged, visibleRange).
- **Adapters.**
  - ProChart, refactored, is the embedded/mini adapter (CH1).
  - The lightweight-charts v5 adapter with our drawing primitives is the full workspace engine for now (CH2).
  - The TradingView adapter comes when access is granted (CH7), behind a per-tenant flag.
- **Drawings** are stored server-side in our own schema (`chart_drawings`): per user and symbol, time/price anchors,
  versioned, lock flag. Each adapter maps them in and out, so switching engines loses nothing.
- **Layers and numbers come from backend endpoints with an `as_of`.** The chart renders data and never derives
  numbers from pixels. Replay requests layers as of each bar.
- **Chart actions create proposals only:** alerts, screens, a PAPER trade proposal (ADR-0006). No engine callback can
  place or modify an order.

## Consequences
- **Cost.** An interface layer to maintain, plus a parity test suite (the same drawings and layers render through each
  adapter).
- **Benefit.** Pages migrate once. TradingView can arrive or not without blocking anything, and A/B parity is testable
  on the same data.
- **New table** `chart_drawings`, with a down-migration.
- **lightweight-charts 4 -> 5 upgrade** (CH2): the attribution link stays on (its licence asks for it).

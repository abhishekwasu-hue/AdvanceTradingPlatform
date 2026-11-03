import type { AbhiRead, BookRead, ReaderResult, ReaderStrategies } from "./types";

/**
 * M86 W3: MADE-UP answers for the tests, the e2e mocks and the screenshots (public repository). The symbol, prices,
 * book names and every sentence are invented, neutral placeholder text; none of it is a real read or a real method.
 */

export const DEMO_SYMBOL = "DEMO";

export function fixtureStrategies(over: Partial<ReaderStrategies> = {}): ReaderStrategies {
  return {
    available: true, reason: null, default: "ABHI", model: "demo-model", effort: "high", estimate_usd: 0.07,
    strategies: [
      { id: "ABHI", name: "Abhi's method", book: "", author: "", edition: "", two_chart: false, sides: ["buyer", "seller"] },
      { id: "DEMO_ONE", name: "Example strategy one", book: "Example Book One", author: "A. Writer", edition: "1901", two_chart: false, sides: ["long"] },
      { id: "DEMO_TWO", name: "Example strategy two", book: "Example Book Two", author: "B. Author", edition: "1902", two_chart: true, sides: ["long", "short"] },
    ],
    ...over,
  };
}

export function fixtureAbhiRead(over: Partial<AbhiRead> = {}): AbhiRead {
  return {
    trend: { direction: "up", evidence: "Placeholder: each swing in this example ends above the one before it." },
    areas: [
      { side: "buyer", price_low: 101.2, price_high: 102.8, chart: "60min", evidence: "Placeholder: the example base where price turned up." },
      { side: "seller", price_low: 109.1, price_high: 110.4, chart: "15min", evidence: "Placeholder: the example top where price turned down." },
    ],
    correction: { present: true, area_index: 0, momentum_weakening: "yes", evidence: "Placeholder: the example pullback slows into the lower area." },
    commitment_candle: { present: false, evidence: "Placeholder: no wide example candle has closed away from the area yet." },
    area_of_interest_statement: "Example area of interest: the lower example range, while the example swings keep rising.",
    watch_next: "Example: watch how the next example candles close around the lower range.",
    cannot_read: null,
    ...over,
  };
}

export function fixtureBookRead(over: Partial<BookRead> = {}): BookRead {
  return {
    side_scored: "long",
    conditions: [
      { id: "c1_example_trend", status: "met", evidence: "Placeholder evidence for the first example condition." },
      { id: "c2_example_range", status: "not_met", evidence: "Placeholder evidence for the second example condition." },
      { id: "c3_example_pair", status: "unclear", evidence: "Placeholder evidence for the third example condition." },
    ],
    levels: [{ kind: "example level", price_low: 104.5, price_high: 105.25, chart: "60min", evidence: "Placeholder evidence for the level." }],
    trigger_seen: { status: "yes", bar: 42, evidence: "Placeholder: the example trigger shape appears on one bar." },
    invalidated: { status: "no", evidence: "Placeholder: nothing in the example breaks the read." },
    statement: "Example statement: the example conditions read as listed on this example chart.",
    cannot_read: null,
    ...over,
  };
}

function base(strategy: { id: string; name: string; independent_book_strategy?: boolean }): Omit<ReaderResult, "read"> {
  return {
    status: "ok", strategy, prompt_version: "demo_v1", removed: [], time_flags: [], two_chart_conditions: [],
    second_chart_missing: false, usage: { input_tokens: 1000, output_tokens: 200, cache_read_input_tokens: 0, model: "demo-model" },
    cached: false, estimate_usd: 0.07, cost_usd: 0.068, label: "Advanced analysis (code+AI)",
  };
}

export function fixtureAbhiResult(over: Partial<ReaderResult<AbhiRead>> = {}): ReaderResult<AbhiRead> {
  return { ...base({ id: "ABHI", name: "Abhi's method" }), read: fixtureAbhiRead(), removed: ["x"], time_flags: ["y", "z"], ...over };
}

export function fixtureBookResult(id = "DEMO_ONE", over: Partial<ReaderResult<BookRead>> = {}): ReaderResult<BookRead> {
  const name = id === "DEMO_TWO" ? "Example strategy two" : "Example strategy one";
  const two = id === "DEMO_TWO";
  return {
    ...base({ id, name, independent_book_strategy: true }), read: fixtureBookRead(),
    two_chart_conditions: two ? ["c3_example_pair"] : [], second_chart_missing: two, ...over,
  };
}

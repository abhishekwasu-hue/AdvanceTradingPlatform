import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { Bell } from "lucide-react";
import MarketStripV2 from "../dashboard/MarketStripV2";
import NeedsAttentionV2, { attentionTone } from "../dashboard/NeedsAttentionV2";
import { ActiveSignalsV2 } from "../dashboard/BlocksV2";
import { lastSessionCloses, sparkBroker } from "../dashboard/indexSparks";
import type { AttentionRow } from "../dashboard/model";
import type { MarketStrip } from "../dashboard/types";
import ChainTableV2 from "../optionChain/ChainTableV2";
import { liveChain } from "../optionChain/chain.fixture";
import { DEFAULT_COLUMNS } from "../optionChain/model";
import { IndexRow, ROW_TONES, StatusChip as SettingsStatusChip } from "../settings/SettingsIndexV2";
import { SETTINGS_ROWS } from "../settings/model";
import BottomNavV2 from "../shell/BottomNavV2";
import { navIconClass, navTone } from "../shell/navTone";
import { SidebarV2View } from "../shell/SidebarV2";
import { visibleSections } from "../shell/navModel";
import { DirectionChip as DayOneDirection } from "../signals/DayOneV2";
import { Direction } from "../signals/SignalPartsV2";
import type { BrokerLogin } from "./BrokerGate";
import { ColourV2Provider, colourV2On, useColourV2 } from "./colourV2";
import { DirectionChip, sideOf } from "./DirectionChip";
import { IconBadge } from "./IconBadge";
import { Sparkline, sparkPath } from "./Sparkline";

/**
 * Colour v2 (owner, 23 Oct; flag `colour_v2`, OFF): the shared pieces and every page that adopts them, rendered with the
 * flag on and off. Off must be exactly today's look (the e2e baselines are unchanged); on shows the new colour.
 */
const noop = () => undefined;
const render = (node: ReactNode, on: boolean) =>
  renderToStaticMarkup(<MemoryRouter><ColourV2Provider on={on}>{node}</ColourV2Provider></MemoryRouter>);

describe("colour v2 switch", () => {
  it("is on only with both ui_v2 and colour_v2 on (fail closed)", () => {
    const flags = (...on: string[]) => (f: string) => on.includes(f);
    expect(colourV2On(flags("ui_v2", "colour_v2"))).toBe(true);
    expect(colourV2On(flags("colour_v2"))).toBe(false);
    expect(colourV2On(flags("ui_v2"))).toBe(false);
    expect(colourV2On(flags())).toBe(false);
  });

  it("is off outside the v2 shell's provider", () => {
    function Probe() { return <span>{useColourV2() ? "on" : "off"}</span>; }
    expect(renderToStaticMarkup(<Probe />)).toBe("<span>off</span>");
    expect(render(<Probe />, true)).toBe("<span>on</span>");
  });
});

describe("colour v2 shared pieces", () => {
  it("IconBadge: a soft circle of the meaning, the icon in the meaning's colour", () => {
    expect(renderToStaticMarkup(<IconBadge icon={Bell} tone="warn" />)).toContain("rounded-pill bg-soft-warn text-warn");
    expect(renderToStaticMarkup(<IconBadge icon={Bell} tone="teal" />)).toContain("bg-soft-teal text-teal-accent");
    expect(renderToStaticMarkup(<IconBadge icon={Bell} tone="neutral" />)).toContain("bg-surface-2 text-fg-muted");
    expect(renderToStaticMarkup(<IconBadge icon={Bell} tone="ai" />)).toMatch(/^<span aria-hidden="true"/);
  });

  it("DirectionChip: Long green with an up arrow, Short red with a down arrow - icon and word, no buy / sell", () => {
    const long = renderToStaticMarkup(<DirectionChip side="long" />);
    const short = renderToStaticMarkup(<DirectionChip side="short" />);
    expect(long).toContain("bg-up-tint text-up");
    expect(long).toContain("lucide-arrow-up-right");
    expect(long).toMatch(/>Long<\/span>$/);
    expect(short).toContain("bg-down-tint text-down");
    expect(short).toContain("lucide-arrow-down-right");
    expect(short).toMatch(/>Short<\/span>$/);
    for (const h of [long, short]) expect(h).not.toMatch(/buy|sell|target|stop/i);
    expect(sideOf("LONG")).toBe("long");
    expect(sideOf("Sell")).toBe("short");
    expect(sideOf("NO_TRADE")).toBeNull();
  });

  it("Sparkline: real values only - nothing with fewer than two, up / down stroke, hidden from screen readers", () => {
    expect(sparkPath([], 96, 28)).toBeNull();
    expect(sparkPath([1], 96, 28)).toBeNull();
    expect(sparkPath([1, Number.NaN], 96, 28)).toBeNull();
    expect(sparkPath([1, 2, 3], 96, 28)).toBe("M0.0 26.0 L48.0 14.0 L96.0 2.0");
    expect(sparkPath([5, 5], 96, 28)).toBe("M0.0 14.0 L96.0 14.0");
    expect(renderToStaticMarkup(<Sparkline values={[1]} tone="up" />)).toBe("");
    const up = renderToStaticMarkup(<Sparkline values={[1, 3, 2]} tone="up" />);
    expect(up).toContain('aria-hidden="true"');
    expect(up).toContain("stroke-up");
    expect(renderToStaticMarkup(<Sparkline values={[3, 1]} tone="down" />)).toContain("stroke-down");
  });

  it("navigation: one muted colour per group, brand for Broker, violet for the AI Copilot only", () => {
    expect(navTone("broker")).toBe("brand");
    expect(navTone("dashboard")).toBe("info");
    expect(navTone("option-chain")).toBe("info");
    expect(navTone("signals")).toBe("teal");
    expect(navTone("positions")).toBe("teal");
    expect(navTone("analytics")).toBe("insights");
    expect(navTone("copilot")).toBe("ai");
    expect(navTone("settings")).toBe("neutral");
    expect(navTone(null)).toBe("neutral");
    expect(navIconClass("signals", false)).toBe("text-fg-muted");
    expect(navIconClass("signals", true)).toBe("text-teal-accent");
  });
});

describe("colour v2 on the pages (off = as before)", () => {
  it("Settings: each row's icon in a soft circle by meaning; off keeps the square neutral icon", () => {
    expect(ROW_TONES).toMatchObject({ broker: "brand", ai: "ai", alerts: "warn", risk: "down", appearance: "teal", defaults: "info", account: "neutral", team: "neutral" });
    const broker = SETTINGS_ROWS.find((r) => r.id === "broker")!;
    const on = render(<IndexRow row={broker} chip={{ text: "Upstox connected", tone: "ok" }} />, true);
    const off = render(<IndexRow row={broker} chip={{ text: "Upstox connected", tone: "ok" }} />, false);
    expect(on).toContain('data-icon-tone="brand"');
    expect(on).toContain("bg-soft-brand text-brand");
    expect(off).not.toContain("data-icon-tone");
    expect(off).toContain("rounded-control bg-surface-2 text-fg-muted");
  });

  it("Settings chips: neutral with a green check when good, amber when it needs action; off keeps today's chips", () => {
    const ok = render(<SettingsStatusChip chip={{ text: "Connected", tone: "ok" }} />, true);
    expect(ok).toContain("bg-surface-2 text-fg-muted");
    expect(ok).toMatch(/text-up"><svg[^>]*lucide-check/);
    expect(render(<SettingsStatusChip chip={{ text: "Log in today", tone: "warn" }} />, true)).toContain("bg-warn-tint text-warn");
    expect(render(<SettingsStatusChip chip={{ text: "Off", tone: "off" }} />, true)).toContain("border border-border text-fg-muted");
    expect(render(<SettingsStatusChip chip={{ text: "Connected", tone: "ok" }} />, false)).toContain("border-up/40 bg-up/10 text-up");
  });

  it("Today: Needs attention icons by type; off keeps the old squares", () => {
    const row = (id: string, icon: AttentionRow["icon"]): AttentionRow => ({ id, icon, tone: "neutral", title: id, detail: "" });
    expect(attentionTone(row("approvals", "approval"))).toBe("brand");
    expect(attentionTone(row("paused", "paused"))).toBe("warn");
    expect(attentionTone(row("alerts", "alert"))).toBe("teal");
    expect(attentionTone(row("failing", "paused"))).toBe("down");
    expect(attentionTone(row("lockout", "lockout"))).toBe("down");
    expect(attentionTone(row("data-delayed", "warning"))).toBe("warn");
    expect(attentionTone(row("email", "account"))).toBe("neutral");
    const idle: BrokerLogin = { busy: null, error: null, codeStep: null, login: noop, submitCode: noop };
    const rows = [row("approvals", "approval"), row("alerts", "alert")];
    const on = render(<NeedsAttentionV2 rows={rows} loginBroker={null} login={idle} />, true);
    expect(on).toContain('data-icon-tone="brand"');
    expect(on).toContain('data-icon-tone="teal"');
    expect(render(<NeedsAttentionV2 rows={rows} loginBroker={null} login={idle} />, false)).not.toContain("data-icon-tone");
  });

  const strip: MarketStrip = {
    source: "live", broker: { broker: "upstox", account_label: "primary" }, refresh_seconds: 15, fetched_at: "2026-10-19T05:30:00Z",
    market: { exchange: "NSE", phase: "open", is_open: true, opens_at: "09:15", closes_at: "15:30", next_open: null, holiday: null, reason: null },
    indices: [
      { key: "NIFTY 50", label: "NIFTY 50", symbol: "NIFTY 50", exchange: "NSE", ltp: 24812.35, prev_close: 24693.95, change: 118.4, change_pct: 0.48,
        as_of: "2026-10-19T05:30:00Z", stale: false, source: "live", missing: false },
      { key: "INDIA VIX", label: "INDIA VIX", symbol: "INDIA VIX", exchange: "NSE", ltp: 13.42, prev_close: 13.63, change: -0.21, change_pct: -1.54,
        as_of: "2026-10-19T05:30:00Z", stale: false, source: "live", missing: false },
    ],
  };
  const stripView = (total: number, sparks?: Record<string, number[]>) => (
    <MarketStripV2 strip={{ status: "ok", data: strip }} oi={{}} marketText={null} broker={null} pnl={{ total, booked: null, open: null }}
      nowMs={Date.parse("2026-10-19T11:00:00+05:30")} sparks={sparks} />
  );

  it("Today: the P&L on a tint of its sign and a sparkline per index; off (or no data) shows neither", () => {
    const sparks = { "NIFTY 50": [24700, 24760, 24812], "INDIA VIX": [13.6, 13.42] };
    const on = render(stripView(6846.5, sparks), true);
    expect(on).toContain('data-pnl-tint="up"');
    expect(on).toContain("bg-up-tint");
    expect(on).toContain('data-sparkline="up"');
    expect(on).toContain('data-sparkline="down"');
    expect(render(stripView(-120, sparks), true)).toContain('data-pnl-tint="down"');
    expect(render(stripView(0, sparks), true)).not.toContain("data-pnl-tint");
    expect(render(stripView(6846.5), true)).not.toContain("data-sparkline");
    const off = render(stripView(6846.5, sparks), false);
    expect(off).not.toContain("data-sparkline");
    expect(off).not.toContain("data-pnl-tint");
  });

  it("Today (CV-4): on a phone the P&L leads the strip, above the indices; from md up and with the flag off as before", () => {
    const pnl = (html: string) => /<div data-pnl="true"[^>]*class="([^"]*)"/.exec(html)?.[1] ?? "";
    expect(pnl(render(stripView(6846.5, { "NIFTY 50": [1, 2] }), true)).split(" ")).toContain("max-md:order-first");
    expect(pnl(render(stripView(0), true)).split(" ")).toContain("max-md:order-first");
    expect(pnl(render(stripView(6846.5), false))).not.toContain("order-");
  });

  it("Today: the sparkline reads the last session of real broker candles, and nothing from market memory", () => {
    const bar = (ts: string, close: number) => ({ timestamp: ts, open: close, high: close, low: close, close, volume: 0 });
    expect(lastSessionCloses([])).toEqual([]);
    expect(lastSessionCloses([bar("2026-10-19T04:00:00Z", 3), bar("2026-10-16T09:00:00Z", 1), bar("2026-10-19T03:45:00Z", 2)])).toEqual([2, 3]);
    expect(sparkBroker(strip)).toBe("upstox");
    expect(sparkBroker({ ...strip, source: "memory" })).toBeNull();
    expect(sparkBroker({ ...strip, broker: null })).toBeNull();
    expect(sparkBroker(null)).toBeNull();
  });

  it("Today and Signals: Long / Short chips and an amber Waiting; off keeps the coloured words and the brand chip", () => {
    const data = { status: "ok" as const, total: 1, rows: [{ id: 1, time: "10:42", symbol: "NIFTY 50", side: "Long" as const, status: "Waiting for you" as const, waiting: true, setup: "", source: "Paper bot" }] };
    const on = render(<ActiveSignalsV2 data={data} />, true);
    expect(on).toContain('data-direction="long"');
    expect(on).toContain("bg-warn-tint text-warn");
    const off = render(<ActiveSignalsV2 data={data} />, false);
    expect(off).not.toContain("data-direction");
    expect(off).toContain("bg-brand/10 text-brand");
    expect(render(<Direction value="SHORT" />, true)).toContain('data-direction="short"');
    expect(render(<Direction value="SHORT" className="block text-t12" />, true)).toMatch(/^<span class="mt-space-1 flex"><span data-direction="short"/);
    expect(render(<Direction value="LONG" />, false)).toBe('<span class="font-semibold text-up">Long</span>');
    expect(render(<Direction value={null} />, true)).toBe("");
  });

  it("Signals day one: the rows take the one shared direction chip; off keeps the day-one chip as it was", () => {
    expect(render(<DayOneDirection value="LONG" />, true)).toContain('data-direction="long"');
    expect(render(<DayOneDirection value="SHORT" />, true)).toContain("bg-down-tint text-down");
    const off = render(<DayOneDirection value="SHORT" />, false);
    expect(off).not.toContain("data-direction");
    expect(off).toContain("bg-down/10 text-down");
  });

  const chain = (on: boolean) => render(<ChainTableV2 live={liveChain()} prefs={DEFAULT_COLUMNS} centreKey="NIFTY|2026-11-03" onPick={noop} />, on);
  const cells = (html: string, strike: number) => {
    const row = new RegExp(`<tr[^>]*data-strike="${strike}"[^>]*>([\\s\\S]*?)</tr>`).exec(html);
    return { row: row?.[0] ?? "", tds: row?.[1].match(/<td[\s\S]*?<\/td>/g) ?? [] };
  };

  it("Option chain (CV-3): no side tint; ITM one neutral step on both sides; red / green OI bars; a brand ATM row", () => {
    const html = chain(true);
    // Spot 24,480: 24,200 is an ITM call and an OTM put; 24,800 an OTM call and an ITM put; 24,500 is the ATM row.
    expect(html).not.toMatch(/chain-call|chain-put|bg-up-tint|bg-down-tint|bg-up\/|bg-down\//);
    const low = cells(html, 24200).tds;
    const high = cells(html, 24800).tds;
    for (const td of [low[0], high[high.length - 1]]) expect(td).toMatch(/^<td[^>]*class="[^"]*bg-surface-2/);
    for (const td of [low[low.length - 1], high[0]]) expect(td).not.toMatch(/^<td[^>]*class="[^"]*bg-surface-2/);
    // The same neutral step as with the flag off, cell for cell.
    const off = chain(false);
    const shaded = (h: string) => [24200, 24800].flatMap((k) => cells(h, k).tds.map((td) => /^<td[^>]*class="[^"]*bg-surface-2/.test(td)));
    expect(shaded(html)).toEqual(shaded(off));
    expect(html).toContain('data-oi-bar="call" class="absolute inset-y-1 right-0 border-b-2 border-oi-call');
    expect(html).toContain('data-oi-bar="put" class="absolute inset-y-1 left-0 border-b-2 border-oi-put');
    expect(html).not.toMatch(/bg-brand\/15"[^>]*data-oi-bar|data-oi-bar[^>]*bg-brand\//);
    const atm = cells(html, 24500);
    expect(atm.row).toMatch(/^<tr[^>]*class="[^"]*bg-brand-tint/);
    for (const td of atm.tds) expect(td).not.toMatch(/bg-surface-2/);
  });

  it("Option chain off: exactly today's shading (ITM surface-2, brand OI bars, the brand ATM row)", () => {
    const html = chain(false);
    expect(html).not.toMatch(/chain-call|chain-put|oi-call|oi-put|brand-tint/);
    expect(cells(html, 24200).tds[0]).toContain("bg-surface-2");
    expect(html).toContain("bg-brand/15");
    expect(cells(html, 24500).row).toContain("bg-brand/10");
  });

  it("Navigation: group-coloured icons and the active bottom-bar tab on a brand pill; off as before", () => {
    const sections = visibleSections({ on: () => false, role: "OWNER" });
    const sideOn = render(<SidebarV2View sections={sections} current="dashboard" user={null} accountPath="/login" />, true);
    // UI-H CA3: the group colour now sits in each section's round tint chip (src/ui/accents.ts).
    expect(sideOn).toContain("bg-sec-trading-soft text-sec-trading");
    expect(sideOn).toContain("bg-sec-insights-soft text-sec-insights");
    const sideOff = render(<SidebarV2View sections={sections} current="dashboard" user={null} accountPath="/login" />, false);
    expect(sideOff).not.toMatch(/text-(?:teal-accent|nav-insights|info)\b|data-section-chip/);
    const barOn = render(<BottomNavV2 current="signals" moreOpen={false} onMore={noop} />, true);
    expect(barOn.match(/data-nav-pill="true"/g)).toHaveLength(1);
    // UI-H CA6 (owner, 25 Oct; supersedes CV-1's neutral grey tabs): every tab on its section's soft round chip, the tab
    // on screen on the strong tint (the solid section colour) with its word in that colour, bold.
    expect(barOn).toMatch(/data-nav-pill="true" data-nav-tint="trading" class="[^"]*bg-sec-trading text-surface-1/);
    expect(barOn.match(/data-nav-tint="[a-z]+" class="[^"]*bg-sec-[a-z]+-soft text-sec-[a-z]+/g)).toHaveLength(4);
    expect(barOn).toMatch(/class="[^"]*text-sec-trading font-semibold"[^>]*>.*Signals/);
    expect(barOn).not.toContain("bg-brand-tint");
    for (const current of ["dashboard", "charts", "positions", null] as const) {
      const bar = render(<BottomNavV2 current={current} moreOpen={false} onMore={noop} />, true);
      expect(bar.match(/font-semibold/g) ?? [], String(current)).toHaveLength(current ? 1 : 0);
      expect(bar.match(/data-nav-pill="true"/g) ?? [], String(current)).toHaveLength(current ? 1 : 0);
    }
    // CV-2: every word on one line (the bold active one included).
    expect(barOn.match(/<span data-nav-label="true" class="whitespace-nowrap">/g)).toHaveLength(5);
    const barOff = render(<BottomNavV2 current="signals" moreOpen={false} onMore={noop} />, false);
    expect(barOff).not.toContain("data-nav-pill");
    expect(barOff).not.toContain("brand-tint");
  });
});

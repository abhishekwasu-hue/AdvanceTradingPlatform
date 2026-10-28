import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import pageSource from "./PaperBotV2.tsx?raw";
import { BotPositionsV2, botOpenPnl, botPositions, exitBotConsequence } from "./BotPositionsV2";
import { lastSignal, whyNoTrade } from "./model";
import { exposure, trade } from "../trading/trading.fixture";

/** UI-28 U2.4 (owner, 28 Oct): each Paper bot shows its own open P&L, its legs and one exit for its positions. */
describe("UI-28 U2.4 a bot's own positions", () => {
  const mine = trade({ id: 201, deployment_id: 9 });
  const other = trade({ id: 202, deployment_id: 3 });
  const closed = trade({ id: 203, deployment_id: 9, exit_time: "2026-10-28T08:00:00Z" });

  it("only the bot's open positions; nothing for a bot that holds none", () => {
    expect(botPositions([mine, other, closed], 9).map((p) => p.id)).toEqual([201]);
    expect(botPositions(null, 9)).toEqual([]);
    expect(renderToStaticMarkup(<MemoryRouter><BotPositionsV2 positions={[]} exposure={null} canTrade onExit={async () => 0} /></MemoryRouter>)).toBe("");
  });

  it("open P&L is Signed from live prices; a missing price is one amber chip, never a partial sum", () => {
    const pnl = botOpenPnl([mine], exposure());
    const html = renderToStaticMarkup(<MemoryRouter><BotPositionsV2 positions={[mine]} exposure={exposure()} canTrade onExit={async () => 1} /></MemoryRouter>);
    if (pnl === null) expect(html).toContain('data-chip="no-price"');
    else expect(html).toMatch(/data-signed="(up|down|flat)"/);
    expect(botOpenPnl([mine], null)).toBeNull();
    expect(renderToStaticMarkup(<MemoryRouter><BotPositionsV2 positions={[mine]} exposure={null} canTrade onExit={async () => 1} /></MemoryRouter>))
      .toContain("No live price");
  });

  it("each leg is a chip that opens My positions; the exit is only for a trader and asks first with the ₹ impact", () => {
    const html = renderToStaticMarkup(<MemoryRouter><BotPositionsV2 positions={[mine]} exposure={null} canTrade onExit={async () => 1} /></MemoryRouter>);
    expect(html).toMatch(/<a[^>]*href="\/positions"[^>]*>.*data-chip="bot-leg"/);
    expect(html).toContain("Exit this bot&#x27;s positions");
    expect(renderToStaticMarkup(<MemoryRouter><BotPositionsV2 positions={[mine]} exposure={null} canTrade={false} onExit={async () => 1} /></MemoryRouter>))
      .not.toContain("data-bot-exit");
    expect(exitBotConsequence(2, -1250.5)).toBe("This closes 2 paper positions at the current price. Open P&L now: −₹1,251.");
    expect(exitBotConsequence(1, null)).toBe("This closes 1 paper position at the current price.");
    // The same Exit now as My positions, one after another; a failure never stops the next exit.
    expect(pageSource).toMatch(/for \(const p of list\) \{\s*try \{ await api\.exitNow\(p\.id\); closed \+= 1; \} catch/);
  });

  it("the row says when the bot last signalled and why a running bot is not trading", () => {
    const now = Date.parse("2026-10-28T10:00:00Z");
    expect(lastSignal(null, now)).toBe("No signal yet");
    expect(lastSignal("2026-10-28T09:48:00Z", now)).toBe("Last signal 12 min ago");
    expect(whyNoTrade({ status: "ACTIVE", consecutive_failures: 2, last_error: "x" } as never)).toBe("Last check failed · retrying");
    expect(whyNoTrade({ status: "ACTIVE", consecutive_failures: 0, last_error: null } as never)).toBeNull();
    expect(whyNoTrade({ status: "PAUSED", consecutive_failures: 3, last_error: "x" } as never)).toBeNull();
  });
});

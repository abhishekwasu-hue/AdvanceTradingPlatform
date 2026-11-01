// M76 MP-0 (docs/design/M76_REVIEW.md section 8, "Metrics"): the baseline of today's tickets before any check card -
// how many warning surfaces ("cards") and warning lines the user sees when he presses Place, and how long the ticket
// took from opening to Place. One report per ticket (POST /api/ui/ticket-metrics, counted in /metrics): closed labels
// and numbers only - no user, no instrument, no price, no text. Best effort and never awaited: Place does not wait for
// it, a failed report changes nothing on the ticket. No UI change.
//
// What counts as a surface (the same selectors e2e/ticket-metrics-baseline-v2.e2e.ts counts in the page):
// - "Before you place" with at least one warn line (`[data-risk-part=warn]`): one card, one line per warn line;
// - the refusals list (`[data-ticket=refusals]`, `[data-basket=confirm-refusals]`): one card, one line per text;
// - the warnings list (`[data-ticket=warnings]`): one card, one line per text;
// - each basket leg's own issue list: one card each;
// - the "cannot place" line or block (`blockedBy` / `basketBlockedBy`): one card, one line.
// Lines are all visible today (nothing is folded behind "more"), so lines_visible = lines_total.

import { request } from "../api/client";
import type { RiskLine } from "./insights";
import { basketBlockedBy, blockedBy } from "./ticketModel";

export type TicketSurfaceKind = "ticket" | "basket";
export type TicketOutcome = "placed" | "closed";

export interface SurfaceCount { cards: number; lines: number; /** M76 MP-2: lines in view (the card folds the rest); absent = all. */ visible?: number }

const EMPTY: SurfaceCount = { cards: 0, lines: 0 };

function add(c: SurfaceCount, lines: number): SurfaceCount {
  return lines > 0 ? { cards: c.cards + 1, lines: c.lines + lines } : c;
}

const warnLines = (lines: readonly (RiskLine | null)[]) => lines.filter((l) => l?.tone === "warn").length;

/** The single-order ticket's Review step (OrderTicketV2 `Review` + the action bar's "cannot place" line). */
export function ticketSurfaces(p: { can_place: boolean; errors: string[]; refusals: string[]; warnings: string[]; risk?: { blocks?: unknown[] } | null },
  risk: readonly (RiskLine | null)[]): SurfaceCount {
  let c = add(EMPTY, warnLines(risk));
  c = add(c, p.errors.length + p.refusals.length);
  c = add(c, p.warnings.length);
  const block = p.risk?.blocks?.[0];
  if ((block && !p.errors.length) || blockedBy(p)) c = add(c, 1);
  return c;
}

/**
 * M76 MP-2 (flag `trade_warnings`): with the ONE check card, every warning line is in it - one card when it has a line,
 * the lines folded past three. Blocks (exchange errors, his own risk limits' refusals, the "cannot place" line) stay
 * outside it and count as before (they are not warnings; at Place there are none, since they hold Place back).
 */
export function cardSurfaces(lines: number, visible: number, blocks: SurfaceCount = EMPTY): SurfaceCount {
  const card = lines > 0 ? 1 : 0;
  return { cards: card + blocks.cards, lines: lines + blocks.lines, visible: Math.min(visible, lines) + blocks.lines };
}

/** The single-order ticket's blocks only (refusals, errors, the "cannot place" line / Risk limits block). */
export function ticketBlocks(p: { can_place: boolean; errors: string[]; refusals: string[]; risk?: { blocks?: unknown[] } | null }): SurfaceCount {
  return ticketSurfaces({ ...p, warnings: [] }, []);
}

/** The basket's blocks only (each leg's issue list, the basket's refusals, the "cannot place" line). */
export function basketBlocks(p: { can_execute: boolean; refusals: string[]; legs: { errors: string[]; refusals: string[] }[] }): SurfaceCount {
  return basketSurfaces(p, []);
}

/** The basket's confirm step (BasketPanelV2 `ConfirmView`). */
export function basketSurfaces(p: { can_execute: boolean; refusals: string[]; legs: { errors: string[]; refusals: string[] }[] },
  risk: readonly (RiskLine | null)[]): SurfaceCount {
  let c = add(EMPTY, warnLines(risk));
  for (const leg of p.legs) c = add(c, leg.errors.length + leg.refusals.length);
  c = add(c, p.refusals.length);
  if (basketBlockedBy(p)) c = add(c, 1);
  return c;
}

export interface TicketReport {
  surface: TicketSurfaceKind; device: "phone" | "desktop"; outcome: TicketOutcome;
  cards_shown: number; lines_total: number; lines_visible: number;
  ms_open_to_place: number | null; ms_review_to_place: number | null; checks_ms: number | null; pending_at_place: number;
}

type Send = (body: TicketReport) => void;
const PHONE_MAX_WIDTH = 767;
const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/**
 * One ticket's timings. `opened` when the ticket opens; `reviewStarted` / `reviewed` around the server check (today's
 * checks run in the preview, so `checks_ms` is the preview's time and nothing is pending at Place); `placed` when Place
 * is pressed (sends the report, with what is shown at that moment); `shown` keeps what the Review step shows; `closed`
 * when the ticket ends without Place after a Review (sends it once, no timings). One report per ticket.
 */
export class TicketClock {
  private openAt: number | null = null;
  private reviewAt: number | null = null;
  private checksMs: number | null = null;
  private last: SurfaceCount | null = null;
  private sent = false;

  constructor(private readonly surface: TicketSurfaceKind, private readonly send: Send = sendReport, private readonly clock: () => number = now) {}

  opened(): void {
    this.openAt = this.clock();
    this.reviewAt = null;
    this.checksMs = null;
    this.last = null;
    this.sent = false;
  }

  reviewStarted(): void { this.reviewAt = this.clock(); }

  /** The server's answer arrived and the Review / confirm step is on screen. */
  reviewed(): void {
    const t = this.clock();
    if (this.reviewAt !== null) this.checksMs = t - this.reviewAt;
    this.reviewAt = t;
  }

  /** What the Review step shows now (its lines fill in as the budget and the events calendar are read). */
  shown(count: SurfaceCount | null): void { if (count) this.last = count; }

  /** `extra` (M76 MP-2): the check card's checks still running at Place and the time its checks took. */
  placed(count?: SurfaceCount, extra?: { pending?: number; checksMs?: number | null }): void {
    if (this.sent) return;
    const t = this.clock();
    const c = count ?? this.last ?? EMPTY;
    if (extra?.checksMs !== undefined && extra.checksMs !== null) this.checksMs = extra.checksMs;
    this.report("placed", c, this.openAt === null ? null : t - this.openAt, this.reviewAt === null ? null : t - this.reviewAt, extra?.pending ?? 0);
  }

  closed(): void {
    if (this.sent || this.last === null) return;   // never reviewed: nothing was shown, nothing to count
    this.report("closed", this.last, null, null);
  }

  private report(outcome: TicketOutcome, c: SurfaceCount, open: number | null, review: number | null, pending = 0): void {
    this.sent = true;
    const r = (v: number | null) => (v === null ? null : Math.max(0, Math.round(v)));
    try {
      this.send({ surface: this.surface, device: deviceOf(), outcome, cards_shown: c.cards, lines_total: c.lines,
        lines_visible: Math.min(c.visible ?? c.lines, c.lines), ms_open_to_place: r(open), ms_review_to_place: r(review), checks_ms: r(this.checksMs),
        pending_at_place: pending });
    } catch { /* intentional: metrics are best effort */ }
  }
}

function deviceOf(): "phone" | "desktop" {
  return typeof window !== "undefined" && window.innerWidth <= PHONE_MAX_WIDTH ? "phone" : "desktop";
}

/** Fire and forget: never awaited by the ticket, a failure (signed out, offline, rate limited) is ignored. */
export function sendReport(body: TicketReport): void {
  try {
    void request<void>("/ui/ticket-metrics", { method: "POST", body: JSON.stringify(body), keepalive: true }).catch(() => {});  // intentional: metrics are best effort
  } catch { /* intentional: metrics are best effort */ }
}

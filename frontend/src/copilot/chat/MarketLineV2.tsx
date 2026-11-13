import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import { formatDateIST, formatPrice, formatTimeIST } from "../../ui/format";
import { lastCloseText } from "../../ui/lastClose";
import { LevelChip } from "../../ui/LevelChip";
import { Signed } from "../../ui/Signed";
import { vixBand } from "../../dashboard/model";
import type { MarketStrip, StripIndex } from "../../dashboard/types";
import type { Loadable } from "../../dashboard/useDashboardData";
import { Skeleton } from "../../components/primitives/Feedback";
import { ConnectLiveButton } from "../../ui/ConnectLive";
import { useCopilotT } from "../i18n";
import { NUMBERS_INDICES } from "./model";

/**
 * Owner 13 Nov (chat redesign): the Copilot's market numbers as ONE compact line of pills - NIFTY 50, BANK NIFTY,
 * SENSEX (last, signed change %), India VIX with its level chip, and FII net. Colour by meaning (the up / down tokens
 * with the sign and arrow, never colour alone). A value that is missing is LEFT OUT, never shown as "–"; a value that is
 * not live carries its "as of" (a remembered level's time, the last close's date). One scrolling line on a phone, a
 * wrapping row from md up; a tap folds it away (remembered on this device).
 */

export interface MarketPill {
  key: string;
  label: string;
  value: string;
  /** In percent units; null: the change is not known (left out). */
  pct: number | null;
  /** "as of 12:10" / "Last close · 12 Nov"; null when the price is live. */
  asOf: string | null;
  vix?: ReturnType<typeof vixBand>;
}

const istDay = (ms: number) => new Date(ms + 5.5 * 3600_000).toISOString().slice(0, 10);

/** "as of 12:10" (today) / "as of 12 Nov" (an older day) / "Last close · 12 Nov"; null for a live price. Pure. */
export function asOfText(index: StripIndex, nowMs: number): string | null {
  if (index.source === "eod") return lastCloseText(index.as_of);
  if (index.source === "live" && !index.stale) return null;
  const at = index.as_of ? Date.parse(index.as_of) : NaN;
  if (!Number.isFinite(at)) return null;
  return istDay(at) === istDay(nowMs) ? `as of ${formatTimeIST(at, { suffix: false })}` : `as of ${formatDateIST(at, { year: false })}`;
}

/** The pills to show: only indices with a real price, each change only when known, VIX with its band. Pure. */
export function marketPills(strip: MarketStrip | null, nowMs: number): MarketPill[] {
  if (!strip) return [];
  const wanted = [...NUMBERS_INDICES, { key: "INDIA VIX", label: "India VIX" }];
  return wanted.flatMap(({ key, label }) => {
    const i = strip.indices.find((x) => x.key === key);
    if (!i || i.missing || i.ltp === null) return [];
    const pill: MarketPill = { key, label, value: formatPrice(i.ltp), pct: key === "INDIA VIX" ? null : i.change_pct ?? null, asOf: asOfText(i, nowMs) };
    if (key === "INDIA VIX") pill.vix = vixBand(i.ltp);
    return [pill];
  });
}

const PILL = "inline-flex min-h-8 shrink-0 items-center gap-space-1 whitespace-nowrap rounded-pill border border-subtle bg-surface-2 px-space-3 text-t13 text-fg";
const STORE = "atp_copilot_market_folded";

function readFolded(): boolean {
  try { return localStorage.getItem(STORE) === "1"; } catch { return false; }
}

export function MarketLineV2({ strip, fii, fiiAsOf, nowMs, startFolded, connect = false }: {
  strip: Loadable<MarketStrip>; fii: number | null; fiiAsOf?: string | null; nowMs: number;
  /** M86 D3: no broker stored - the prices are the last close; one in-place "connect for live prices" button here. */
  connect?: boolean;
  /** A focused chat starts folded (the item comes first); the general chat as the person left it. */
  startFolded?: boolean;
}) {
  const t = useCopilotT();
  const [folded, setFolded] = useState(() => startFolded ?? readFolded());
  const toggle = () => {
    setFolded((f) => {
      if (startFolded === undefined) { try { localStorage.setItem(STORE, f ? "0" : "1"); } catch { /* intentional: the fold is a convenience */ } }
      return !f;
    });
  };
  const pills = marketPills(strip.status === "ok" ? strip.data : null, nowMs);
  return (
    <section aria-label={t("chat.numbers")} data-numbers-strip data-market-line-strip data-folded={folded || undefined} data-tour="copilot-market" className="min-w-0">
      <div className="flex min-w-0 items-center gap-space-2">
        <button type="button" onClick={toggle} aria-expanded={!folded} aria-label={folded ? t("chat.marketShow") : t("chat.marketHide")} data-market-toggle
          className="inline-flex min-h-11 shrink-0 items-center gap-space-1 rounded-pill px-space-2 text-t12 font-semibold uppercase md:min-h-8 text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          {t("chat.marketShow")}{folded ? <ChevronDown size={14} aria-hidden /> : <ChevronUp size={14} aria-hidden />}
        </button>
        {!folded && (
          <ul className="flex min-w-0 flex-1 gap-space-2 overflow-x-auto md:flex-wrap md:overflow-visible" data-market-pills>
            {strip.status === "loading" && <li><Skeleton className="h-8 w-40" /></li>}
            {pills.map((p) => (
              <li key={p.key} className={PILL} data-index={p.key} data-as-of={p.asOf ?? "live"} data-vix-level={p.vix ? p.vix.level.toLowerCase() : undefined}>
                <span className="text-fg-muted">{p.label}</span>
                <span className="font-tabular font-semibold">{p.value}</span>
                {p.pct !== null && <Signed value={p.pct} kind="pct" className="text-t12" />}
                {p.vix && <LevelChip scale="vix" level={p.vix.level} />}
                {p.asOf && <span className="text-t12 text-fg-muted">{p.asOf}</span>}
              </li>
            ))}
            {fii !== null && (
              <li className={PILL} data-fii-net="ok">
                <span className="text-fg-muted">{t("chat.fiiNet")}</span>
                <Signed value={fii} kind="count" decimals={0} /><span className="text-fg-muted">cr</span>
                {fiiAsOf && <span className="text-t12 text-fg-muted">{formatDateIST(fiiAsOf, { year: false })}</span>}
              </li>
            )}
          </ul>
        )}
      </div>
      {!folded && strip.status === "error" && <p className="mt-space-1 text-t13 text-fg-muted">{t("chat.numbersError")}</p>}
      {!folded && connect && <div className="mt-space-2"><ConnectLiveButton /></div>}
    </section>
  );
}

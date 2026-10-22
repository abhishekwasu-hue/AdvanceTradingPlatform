import {
  Bell, ChevronRight, LifeBuoy, ListTodo, Palette, Plug, Search, SlidersHorizontal, Sparkles, UserCircle2, Wrench, X, type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { api } from "../api/client";
import type { AiConnection } from "../aiKeys/connect";
import { AI_CONNECTION_CHANGED_EVENT } from "../aiKeys/connect";
import { useAuth } from "../auth/AuthContext";
import { useBrokerConnections } from "../broker/useBrokerConnections";
import { CARD } from "../dashboard/CardV2";
import type { PageProps } from "../routes";
import { setupChip } from "../setup/model";
import { useSetup } from "../setup/useSetup";
import { useAppearance } from "../theme";
import { useTours } from "../tours/state";
import type { AiProviderConfig, AlertChannel, BillingOverview } from "../types";
import { SIGN_IN_PATH } from "../ui/BrokerGate";
import { useFeatures } from "../ui/useFeatures";
import {
  COMING_SOON, SETTINGS_ROWS, aiChip, aiProviderChip, alertsChip, appearanceChip, brokerChip, planChip, searchSettings, tipsChip,
  type Chip, type RowId, type SearchHit, type SettingsRow,
} from "./model";
import { GO_LIVE_PATH_V2, settingsHashTarget } from "./paths";
import { useLiveHidden } from "./useLiveHidden";

/**
 * Settings redesign part B (docs/design/SETTINGS_REDESIGN_REVIEW.md B1-B5, behind `ui_v2`): Settings is a short index,
 * like a phone's settings - one row per thing (an icon, its name, a live status chip and a chevron), each opening its
 * own small page. A search box on top finds the rows and the settings inside them. The old `/settings#<part>` links
 * forward to the new pages (B4). "Prepare for LIVE" shows only while `phase0_paper_only` is off (B3).
 * With `ui_v2` off, today's Settings page (pages/SettingsPage.tsx) is shown, unchanged.
 */

const ICONS: Record<RowId, LucideIcon> = {
  account: UserCircle2, broker: Plug, ai: Sparkles, alerts: Bell, defaults: SlidersHorizontal, appearance: Palette,
  help: LifeBuoy, setup: ListTodo, advanced: Wrench,
};

const TONE: Record<Chip["tone"], string> = {
  ok: "border-up/40 bg-up/10 text-up",
  warn: "border-warn/40 bg-warn-tint text-warn",
  bad: "border-down/40 bg-down/10 text-down",
  off: "border-border bg-surface-2 text-fg-muted",
};

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand";

export function StatusChip({ chip }: { chip: Chip }) {
  return (
    <span data-chip={chip.tone}
      className={`inline-flex min-h-6 max-w-full items-center rounded-pill border px-space-2 text-t12 font-medium ${TONE[chip.tone]}`}>
      {chip.text}
    </span>
  );
}

/** One row: the icon, the name with its chip (under the name on a phone, beside it from `sm`), the chevron. */
export function IndexRow({ row, chip }: { row: SettingsRow; chip: Chip | null }) {
  const Icon = ICONS[row.id];
  const body = (
    <>
      <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control bg-surface-2 text-fg-muted"><Icon size={18} /></span>
      <span className="flex min-w-0 flex-1 flex-col items-start gap-space-1 sm:flex-row sm:items-center sm:justify-between sm:gap-space-3">
        <span className="text-t14 font-medium text-fg">{row.name}</span>
        {chip && <StatusChip chip={chip} />}
      </span>
      {row.to && <ChevronRight size={18} aria-hidden className="shrink-0 text-fg-muted" />}
    </>
  );
  const cls = `flex min-h-14 w-full items-center gap-space-3 px-space-4 py-space-2 md:px-space-5 ${FOCUS}`;
  return (
    <li data-row={row.id} data-tour={row.tour} className="border-t border-subtle first:border-t-0">
      {row.to
        ? <Link to={row.to} className={`${cls} transition-colors duration-fast hover:bg-surface-2`}>{body}</Link>
        : <div className={cls} aria-disabled="true">{body}</div>}
    </li>
  );
}

/** The chips the rows show, each from the source that page itself reads (one plain read each; nothing polls). */
function useChips(oneKey: boolean): Partial<Record<RowId, Chip | null>> {
  const { user } = useAuth();
  const brokers = useBrokerConnections().rows;
  const [appearance] = useAppearance();
  const tours = useTours(user?.id ?? null);
  const { view } = useSetup(user?.id ?? null);
  const [ai, setAi] = useState<Chip | null>(null);
  const [alerts, setAlerts] = useState<Chip | null>(null);
  const [plan, setPlan] = useState<Chip | null>(null);

  useEffect(() => {
    let live = true;
    const readAi = () => {
      const read: Promise<Chip | null> = oneKey
        ? api.aiConnection().then((c: AiConnection) => aiChip(c))
        : api.aiProvider().then((p: AiProviderConfig) => aiProviderChip(p));
      read.then((c) => { if (live) setAi(c); }, () => { if (live) setAi(null); });
    };
    readAi();
    api.listAlertChannels().then((c: AlertChannel[]) => { if (live) setAlerts(alertsChip(c)); }, () => {});
    api.billingOverview().then((o: BillingOverview) => { if (live) setPlan(planChip(o)); }, () => {});
    window.addEventListener(AI_CONNECTION_CHANGED_EVENT, readAi);
    return () => { live = false; window.removeEventListener(AI_CONNECTION_CHANGED_EVENT, readAi); };
  }, [oneKey]);

  const setup = setupChip(view, { ignoreDismissal: true });
  return {
    account: plan, broker: brokerChip(brokers), ai, alerts, defaults: COMING_SOON, appearance: appearanceChip(appearance),
    help: tipsChip(tours.known, tours.marks.skip_all_at),
    setup: setup ? { text: setup.label, tone: "warn" } : view ? { text: "Done", tone: "ok" } : null,
    advanced: null,
  };
}

function SearchBox({ query, onQuery, onEnter }: { query: string; onQuery: (q: string) => void; onEnter: () => void }) {
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); onEnter(); }
    if (e.key === "Escape") onQuery("");
  };
  return (
    <div className={`${CARD} flex min-h-12 items-center gap-space-2 px-space-3`}>
      <Search size={18} aria-hidden className="shrink-0 text-fg-muted" />
      <input type="search" value={query} onChange={(e) => onQuery(e.target.value)} onKeyDown={onKey} aria-label="Search settings"
        placeholder="Search settings" data-settings-search
        className="min-h-11 min-w-0 flex-1 bg-transparent text-t14 text-fg placeholder:text-fg-muted focus:outline-none [&::-webkit-search-cancel-button]:hidden" />
      {query && (
        <button type="button" onClick={() => onQuery("")} aria-label="Clear the search"
          className={`inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 ${FOCUS}`}>
          <X size={18} aria-hidden />
        </button>
      )}
    </div>
  );
}

function Results({ hits }: { hits: SearchHit[] }) {
  if (hits.length === 0) {
    return <p className={`${CARD} px-space-4 py-space-4 text-t14 text-fg-muted`} role="status" data-search-empty>Nothing matches. Try another word.</p>;
  }
  return (
    <ul className={`${CARD} overflow-hidden`} data-search-results aria-label="Matching settings">
      {hits.map((h) => (
        <li key={`${h.label}-${h.to}`} className="border-t border-subtle first:border-t-0" data-hit={h.label}>
          {h.to ? (
            <Link to={h.to} className={`flex min-h-12 items-center gap-space-3 px-space-4 py-space-2 hover:bg-surface-2 md:px-space-5 ${FOCUS}`}>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="text-t14 font-medium text-fg">{h.label}</span>
                {h.under && <span className="text-t12 text-fg-muted">in {h.under}</span>}
              </span>
              <ChevronRight size={18} aria-hidden className="shrink-0 text-fg-muted" />
            </Link>
          ) : (
            <div className="flex min-h-12 items-center gap-space-3 px-space-4 py-space-2 md:px-space-5">
              <span className="flex-1 text-t14 font-medium text-fg">{h.label}</span>
              <StatusChip chip={COMING_SOON} />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function SettingsIndexV2(_props: PageProps = {}) {
  const { user, loading } = useAuth();
  const features = useFeatures();
  const oneKey = features.on("ai_one_key");
  const liveHidden = useLiveHidden();
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const chips = useChips(oneKey);
  const ctx = useMemo(() => ({ on: features.on, role: user?.role ?? null }), [features.on, user?.role]);
  const hits = useMemo(() => searchSettings(query, ctx), [query, ctx]);

  if (!loading && !user) return <Navigate to={SIGN_IN_PATH} replace />;
  // B4: the old `/settings#alerts`, `#ai`, `#integrations`, `#advanced` and `#go-live` links open the new pages.
  const forward = settingsHashTarget(location.hash, liveHidden);
  if (forward) return <Navigate to={forward} replace />;

  const main = SETTINGS_ROWS.filter((r) => r.id !== "advanced");
  const advanced = SETTINGS_ROWS.find((r) => r.id === "advanced");
  const jump = () => { const first = hits.find((h) => h.to); if (first?.to) navigate(first.to); };

  return (
    <div className="mx-auto w-full max-w-[760px] space-y-space-4" data-page="settings-index-v2">
      <div className="flex flex-wrap items-center justify-between gap-x-space-4 gap-y-space-2">
        <h1 className="text-t20 font-semibold text-fg">Settings</h1>
        {!liveHidden && (
          <Link to={GO_LIVE_PATH_V2} data-prepare-live
            className={`inline-flex min-h-11 items-center gap-space-1 rounded-control text-t14 font-medium text-brand hover:underline ${FOCUS}`}>
            Prepare for LIVE <ChevronRight size={16} aria-hidden />
          </Link>
        )}
      </div>
      <SearchBox query={query} onQuery={setQuery} onEnter={jump} />
      {query.trim() ? <Results hits={hits} /> : (
        <>
          <ul className={`${CARD} overflow-hidden`} aria-label="Settings" data-settings-index>
            {main.map((r) => <IndexRow key={r.id} row={r} chip={chips[r.id] ?? null} />)}
          </ul>
          {advanced && (
            <ul className={`${CARD} overflow-hidden`} aria-label="Advanced settings">
              <IndexRow row={advanced} chip={null} />
            </ul>
          )}
        </>
      )}
    </div>
  );
}

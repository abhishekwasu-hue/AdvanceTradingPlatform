import { ArrowDownRight, ArrowUpRight, ChevronDown, RefreshCw } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { api as mainApi } from "../api/client";
import { ApiError } from "../api/errors";
import { TEMPLATE_ID_PREFIX, botName, botStatusText, lastChecked } from "../paperbot/model";
import type { Deployment } from "../types";
import { cx } from "../components/primitives/cx";
import { CARD, LINK_PRIMARY, LINK_SECONDARY, LoadingRowsV2 } from "../dashboard/CardV2";
import { paperBotPathFor } from "../strategyTemplates/link";
import { useColourV2 } from "../ui/colourV2";
import { DirectionChip as SharedDirectionChip } from "../ui/DirectionChip";
import { formatTimeIST } from "../ui/format";
import {
  STANDS_OUT, STATE_LABEL, dayOneApi, headline, listRef, listValue, rowArea, rowText,
  type DayOne, type DayOneList, type DayOneOffer, type DayOneRow, type DayOneWatch,
} from "./dayOne";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";

type Api = typeof dayOneApi;
type State = { status: "loading" } | { status: "off" } | { status: "error" } | { status: "ok"; data: DayOne };

const BUTTON = "inline-flex min-h-11 items-center justify-center gap-space-2 rounded-control px-space-3 text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50";

/**
 * Signals day one (owner order 23 Oct, "Signals not empty on day one"): the ready-made dish on top of Signals.
 * First visit: "Pick a ready strategy and a list to watch" - two or three ready strategies (white box: the idea and every
 * default setting) and a list (NIFTY 50 by default). He sees the settings before he confirms; one tap adopts it as HIS
 * paper strategy (PAPER only, every entry waits for his Approve) and the list is read once on the latest closed Daily
 * bars. Then: his watch - each symbol's state in structure words, the new setups and the ones waiting for a trigger first.
 * The server answers 404 while `ui_v2` or `strategy_templates` is off: nothing shows.
 */
export default function DayOneV2({ api = dayOneApi, onAdopted, loadBots = () => mainApi.listDeployments(false) }: {
  api?: Api; onAdopted?: (adopted: boolean) => void; loadBots?: () => Promise<Deployment[]>;
}) {
  const [state, setState] = useState<State>({ status: "loading" });
  // Day-one fix 7: the paper bots he already runs (a bot he just started from an idea, for example). With one running
  // and no watch adopted yet, the card shows his bot instead of asking him to adopt a strategy again.
  const [bots, setBots] = useState<Deployment[] | null>(null);
  const [alsoList, setAlsoList] = useState(false);
  const loader = useRef(loadBots);
  useEffect(() => {
    let live = true;
    loader.current().then((b) => { if (live) setBots(Array.isArray(b) ? b : []); }).catch(() => { if (live) setBots([]); });
    return () => { live = false; };
  }, []);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [changing, setChanging] = useState(false);
  const told = useRef(onAdopted);
  told.current = onAdopted;

  const show = (data: DayOne) => { setState({ status: "ok", data }); told.current?.(!!data.adopted); };
  useEffect(() => {
    let live = true;
    api.get().then((d) => { if (live) show(d); })
      .catch((e: unknown) => { if (live) setState({ status: e instanceof ApiError && e.status === 404 ? "off" : "error" }); });
    return () => { live = false; };
  }, [api]);   // eslint-disable-line react-hooks/exhaustive-deps -- `show` only sets state

  const act = (run: () => Promise<DayOne>) => {
    setBusy(true); setProblem(null);
    run().then((d) => { show(d); setChanging(false); })
      .catch((e: unknown) => setProblem(e instanceof Error ? e.message : "That did not work. Try again in a moment."))
      .finally(() => setBusy(false));
  };

  if (state.status === "off" || state.status === "error") return null;      // the page below says what it can itself
  if (state.status === "loading") {
    return <section aria-label="Loading your watch" className={cx(CARD, "p-space-4 md:p-space-5")}><LoadingRowsV2 rows={2} /></section>;
  }
  const data = state.data;
  if (data.adopted && !changing) {
    return <WatchView watch={data.adopted} canAct={data.can_adopt} busy={busy} problem={problem}
      onRefresh={() => act(api.refresh)} onChange={() => { setChanging(true); setProblem(null); }} onStop={() => act(api.stop)} />;
  }
  const running = (bots ?? []).filter((b) => b.mode === "PAPER" && (b.status === "ACTIVE" || b.status === "PAUSED"));
  if (!data.adopted && running.length > 0 && !alsoList) {
    return <BotsView bots={running} offers={data.offers} onAlsoList={data.can_adopt && data.offers.length ? () => setAlsoList(true) : undefined} />;
  }
  return <SetupView data={data} busy={busy} problem={problem}
    onCancel={data.adopted ? () => setChanging(false) : alsoList ? () => setAlsoList(false) : undefined}
    onAdopt={(id, list) => act(() => api.adopt(id, list))} />;
}

/** "Starts on its next round" for a running bot that has not checked yet (a just-started bot), else "Checked ... ago". */
export function botCheckWords(b: Pick<Deployment, "status" | "last_evaluated_at">, now: number = Date.now()): string {
  if (!b.last_evaluated_at) return b.status === "ACTIVE" ? "Just started: it reads its chart on its next round" : "Not checked yet";
  return lastChecked(b.last_evaluated_at, now);
}

/** Day-one fix 7: his own running paper bots, in place of "Pick a ready strategy" (he already picked one). */
export function BotsView({ bots, offers, onAlsoList, now }: {
  bots: Deployment[]; offers: DayOneOffer[]; onAlsoList?: () => void; now?: number;
}) {
  const titleId = useId();
  const names = new Map<string, string>(offers.map((o) => [TEMPLATE_ID_PREFIX + o.id, o.name] as [string, string]));
  const one = bots.length === 1;
  return (
    <section aria-labelledby={titleId} className={cx(CARD, "space-y-space-3 p-space-4 md:p-space-5")} data-day-one="bots" data-tour="signals-day-one">
      <div>
        <h2 id={titleId} className="text-t15 font-semibold text-fg">{one ? "Your paper bot is on" : `Your ${bots.length} paper bots are on`}</h2>
        <p className="mt-space-1 text-t14 text-fg-muted">
          {one ? "It reads its chart by itself. When it finds a setup, the signal shows below" : "They read their charts by themselves. When one finds a setup, the signal shows below"}
          {" "}under Waiting for you or Today. Paper money only.
        </p>
      </div>
      <ul className="divide-y divide-subtle rounded-panel border border-subtle">
        {bots.map((b) => (
          <li key={b.id} className="flex flex-col gap-space-1 px-space-3 py-space-3 md:flex-row md:items-center md:justify-between" data-day-one-bot={b.id}>
            <span className="min-w-0">
              <span className="block text-t14 font-semibold text-fg">{botName(b, names)} · {b.symbol}</span>
              <span className="block text-t13 text-fg-muted">{botStatusText(b)} · {botCheckWords(b, now)}</span>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-space-2">
        <Link to="/deployments" className={LINK_SECONDARY}>Open Paper bot</Link>
        {onAlsoList && <button type="button" className={BUTTON} onClick={onAlsoList} data-day-one-also-list>Also watch a whole list</button>}
      </div>
    </section>
  );
}

/** The first-visit card: pick a ready strategy and a list, see its settings, confirm. */
export function SetupView({ data, busy = false, problem = null, onAdopt, onCancel, initialStep = "pick" }: {
  data: DayOne; busy?: boolean; problem?: string | null; onAdopt: (templateId: string, list: { kind: "system" | "own"; key?: string; id?: number }) => void;
  onCancel?: () => void; initialStep?: "pick" | "confirm";
}) {
  const [picked, setPicked] = useState<string>(data.offers[0]?.id ?? "");
  const [list, setList] = useState<string>(listValue(data.default_list) || (data.lists[0] ? listValue(data.lists[0]) : ""));
  const [step, setStep] = useState<"pick" | "confirm">(initialStep);
  const titleId = useId();
  const listId = useId();
  const offer = data.offers.find((o) => o.id === picked) ?? data.offers[0];
  const chosen = data.lists.find((l) => listValue(l) === list);
  const groups = useMemo(() => groupLists(data.lists), [data.lists]);

  let blocked: string | null = null;
  if (!data.offers.length) blocked = "Ready strategies are not switched on for your account yet.";
  else if (!data.lists.length) blocked = "The index and sector lists are not ready yet. They fill after the next list update.";
  else if (!data.can_adopt) blocked = "Your role can read signals but not pick a strategy. Ask your organisation's owner.";

  return (
    <section aria-labelledby={titleId} className={cx(CARD, "space-y-space-4 p-space-4 md:p-space-5")} data-day-one="setup" data-tour="signals-day-one">
      <div>
        <h2 id={titleId} className="text-t15 font-semibold text-fg">Pick a ready strategy and a list to watch</h2>
        <Details id="signals.day-one" className="mt-space-1">
          <p>
            Your own signals start here: the strategy reads every symbol on your list and shows where each one stands. Paper money only.
          </p>
        </Details>
        {data.outdated && <p className="mt-space-2 text-t14 text-fg">The ready strategy you picked before has been updated. Pick it again.</p>}
      </div>
      {blocked ? (
        <p className="rounded-panel bg-surface-2 px-space-4 py-space-3 text-t14 text-fg" data-day-one-blocked>{blocked}</p>
      ) : step === "pick" || !offer || !chosen ? (
        <>
          <fieldset className="space-y-space-2">
            <legend className="text-t14 font-semibold text-fg">Ready strategy</legend>
            <div className="grid gap-space-2 md:grid-cols-3">
              {data.offers.map((o) => (
                <label key={o.id} className={cx("flex min-h-11 cursor-pointer gap-space-3 rounded-panel border p-space-3",
                  o.id === offer?.id ? "border-brand bg-brand/5" : "border-subtle hover:bg-surface-2")}>
                  <input type="radio" name="day-one-offer" value={o.id} checked={o.id === offer?.id} onChange={() => setPicked(o.id)}
                    className="mt-space-1 h-4 w-4 shrink-0 accent-brand" />
                  <span className="min-w-0">
                    <span className="block text-t14 font-semibold text-fg">{o.name}</span>
                    <span className="block text-t12 text-fg-muted">{o.difficulty} · {o.timeframe} chart</span>
                    <span className="mt-space-1 block text-t14 text-fg-muted">{o.idea}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="space-y-space-1">
            <label htmlFor={listId} className="block text-t14 font-semibold text-fg">List to watch</label>
            <Combobox id={listId} label="List to watch" value={list} onChange={setList} className="w-full md:w-96"
              options={listOptions(groups)} />
            {chosen && chosen.count > data.max_watch && (
              <p className="text-t12 text-fg-muted">The first {data.max_watch} symbols of this list are read.</p>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-space-2">
            <button type="button" className={LINK_PRIMARY} onClick={() => setStep("confirm")} disabled={!offer || !chosen}>See its settings</button>
            {onCancel && <button type="button" className={BUTTON} onClick={onCancel}>Keep my watch</button>}
          </div>
        </>
      ) : (
        <Confirm offer={offer} list={chosen} busy={busy} onBack={() => setStep("pick")} onAdopt={() => {
          const ref = listRef(list);
          if (ref) onAdopt(offer.id, ref);
        }} />
      )}
      {problem && <p role="alert" className="rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg">{problem}</p>}
    </section>
  );
}

function Confirm({ offer, list, busy, onBack, onAdopt }: { offer: DayOneOffer; list: DayOneList; busy: boolean; onBack: () => void; onAdopt: () => void }) {
  return (
    <div className="space-y-space-3" data-day-one="confirm">
      <p className="text-t14 text-fg"><span className="font-semibold">{offer.name}</span> on <span className="font-semibold">{list.name}</span>, {offer.timeframe} chart.</p>
      <p className="text-t14 text-fg-muted">{offer.idea}</p>
      <div>
        <p className="text-t14 font-semibold text-fg">Its settings (the strategy&apos;s own defaults)</p>
        <dl className="mt-space-2 grid gap-x-space-4 gap-y-space-2 rounded-panel bg-surface-2 p-space-3 md:grid-cols-2">
          {offer.settings.map((s) => (
            <div key={s.key} className="min-w-0">
              <dt className="text-t12 text-fg-muted" title={s.help}>{s.label}</dt>
              <dd className="text-t14 font-medium text-fg">{s.value}</dd>
            </div>
          ))}
        </dl>
        {offer.rules.length > 0 && <ul className="mt-space-2 list-disc pl-space-5 text-t12 text-fg-muted">{offer.rules.map((r) => <li key={r}>{r}</li>)}</ul>}
      </div>
      <ul className="space-y-space-1 text-t14 text-fg">
        <li>Paper money only. Nothing is placed: each setup waits for you.</li>
        <li>Signals only. What to trade, how much and when to get out stay your own choices.</li>
        <li>You can change the settings later in a paper bot.</li>
      </ul>
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" className={LINK_PRIMARY} onClick={onAdopt} disabled={busy} data-day-one-adopt>
          {busy ? "Reading your list…" : "Adopt on paper and read my list"}
        </button>
        <button type="button" className={BUTTON} onClick={onBack} disabled={busy}>Back</button>
      </div>
    </div>
  );
}

function groupLists(lists: DayOneList[]): [string, DayOneList[]][] {
  const out = new Map<string, DayOneList[]>();
  for (const l of lists) out.set(l.group, [...(out.get(l.group) ?? []), l]);
  return [...out.entries()];
}

/** The "List to watch" rows, under their groups ("Indices", "My lists"), with the member count in the name. */
export function listOptions(groups: [string, DayOneList[]][]): { value: string; label: string; group: string }[] {
  return groups.flatMap(([group, items]) => items.map((l) => ({ value: listValue(l), label: l.name, group })));
}

export const listGroups = groupLists;

/** His watch: the headline, the rows that stand out, the rest folded. */
export function WatchView({ watch, canAct = true, busy = false, problem = null, onRefresh, onChange, onStop }: {
  watch: DayOneWatch; canAct?: boolean; busy?: boolean; problem?: string | null; onRefresh: () => void; onChange: () => void; onStop: () => void;
}) {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const restId = useId();
  const top = watch.rows.filter((r) => STANDS_OUT.includes(r.state));
  const rest = watch.rows.filter((r) => !STANDS_OUT.includes(r.state));
  const line = headline(watch);
  const read = watch.evaluated_at ? formatTimeIST(watch.evaluated_at, { suffix: false }) : null;
  return (
    <section aria-labelledby={titleId} className={cx(CARD, "space-y-space-3 p-space-4 md:p-space-5")} data-day-one="watch" data-tour="signals-day-one">
      <div className="flex flex-wrap items-start justify-between gap-space-2">
        <div className="min-w-0">
          <h2 id={titleId} className="text-t15 font-semibold text-fg">Your watch: {watch.template.name}</h2>
          <p className="text-t14 text-fg-muted">
            {watch.list.name} · {watch.template.timeframe} chart · Paper{read ? ` · read at ${read}` : ""}
          </p>
        </div>
        {canAct && (
          <button type="button" onClick={onRefresh} disabled={busy} className={BUTTON} aria-label="Read the list again">
            <RefreshCw aria-hidden className={cx("h-4 w-4", busy && "animate-spin")} />Refresh
          </button>
        )}
      </div>
      {line && <p className="text-t15 font-semibold text-fg" data-day-one-headline>{line}</p>}
      {watch.note && <p className="rounded-panel bg-surface-2 px-space-4 py-space-3 text-t14 text-fg" data-day-one-note>{watch.note}</p>}
      {top.length > 0 && <ul className="divide-y divide-subtle" aria-label="Setups on your list">{top.map((r) => <Row key={r.symbol} row={r} />)}</ul>}
      {rest.length > 0 && !watch.note && (
        <div>
          <button type="button" aria-expanded={open} aria-controls={restId} onClick={() => setOpen((o) => !o)} className={cx(BUTTON, "px-0")}>
            <ChevronDown aria-hidden className={cx("h-4 w-4 transition-transform duration-fast", open && "rotate-180")} />
            {open ? "Hide" : "Show"} the other {rest.length} {rest.length === 1 ? "symbol" : "symbols"}
          </button>
          {open && <ul id={restId} className="divide-y divide-subtle" aria-label="The rest of your list">{rest.map((r) => <Row key={r.symbol} row={r} />)}</ul>}
        </div>
      )}
      {watch.truncated && <p className="text-t12 text-fg-muted">The first {watch.max_watch} symbols of this list are read.</p>}
      {problem && <p role="alert" className="rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg">{problem}</p>}
      {canAct && (
        <div className="flex flex-wrap items-center gap-space-2 border-t border-subtle pt-space-3">
          <Link to={paperBotPathFor({ template: watch.template.id, timeframe: "1d", params: {} })} className={LINK_SECONDARY}>Start a paper bot with it</Link>
          <button type="button" className={BUTTON} onClick={onChange} disabled={busy}>Change strategy or list</button>
          <button type="button" className={cx(BUTTON, "text-fg-muted")} onClick={onStop} disabled={busy}>Stop watching</button>
        </div>
      )}
    </section>
  );
}

function Row({ row }: { row: DayOneRow }) {
  const area = rowArea(row);
  const stands = STANDS_OUT.includes(row.state);
  return (
    <li className="flex flex-col gap-space-1 py-space-3 md:flex-row md:items-start md:gap-space-4" data-day-one-row={row.state}>
      <div className="flex shrink-0 flex-wrap items-center gap-space-2 md:w-64">
        <span className="text-t14 font-semibold text-fg">{row.symbol}</span>
        {row.direction && <DirectionChip value={row.direction} />}
        <span className={cx("inline-flex h-6 items-center rounded-pill px-space-2 text-t12 font-medium",
          row.state === "new" ? "bg-brand/10 text-brand" : row.state === "armed" ? "bg-warn-tint text-warn" : "border border-border text-fg-muted")}>
          {STATE_LABEL[row.state]}
        </span>
      </div>
      <div className="min-w-0">
        <p className={cx("text-t14", stands ? "text-fg" : "text-fg-muted")}>{rowText(row)}</p>
        {area && <p className="text-t14 font-medium text-fg">{area}</p>}
      </div>
    </li>
  );
}

/**
 * Long = green with an up arrow, Short = red with a down arrow: icon and word, never colour alone. With colour v2 on
 * (flag `colour_v2`) it is the one shared direction chip (ui/DirectionChip), the same as on every other page.
 */
export function DirectionChip({ value }: { value: "LONG" | "SHORT" }) {
  const colour = useColourV2();
  if (colour) return <SharedDirectionChip side={value === "LONG" ? "long" : "short"} />;
  const up = value === "LONG";
  const Icon = up ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cx("inline-flex h-6 items-center gap-space-1 rounded-pill px-space-2 text-t12 font-semibold", up ? "bg-up/10 text-up" : "bg-down/10 text-down")}>
      <Icon aria-hidden className="h-3.5 w-3.5" />{up ? "Long" : "Short"}
    </span>
  );
}

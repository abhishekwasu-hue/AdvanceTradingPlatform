import { useEffect, useId, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { cx } from "../components/primitives/cx";
import { Combobox } from "../ui/Combobox";
import { EMPTY, formatDateIST, formatPrice } from "../ui/format";
import {
  FRAME_MS, SPEEDS, clampFrame, formatLakhs, formatLakhsChange, hhmm, historyApi, nextFrame, pickerTime, rangeNote, replayRows, replayScale, rowsAround, usedNote,
  type CoverageDay, type RangeReply, type ReplayReply, type Speed,
} from "./history";
import { strikeText } from "./oiTab";

/**
 * M96 N1: OI over a From-To range and its replay, from the user's OWN recorded option chains (the 1-minute chain store
 * and his own 5-minute reads; never another person's, never sample data).
 * - "OI change between two times": pick the day, the expiry and two times inside the stored readings; per strike the
 *   call and put OI change between them and put − call (green up / red down).
 * - "Replay": every stored reading of that day, a time slider and play / pause with a speed; per strike the OI at that
 *   reading and its change since the day's first one (only readings up to the one shown).
 * Facts only - no order path here.
 */
const CARD = "min-w-0 rounded-panel border border-subtle bg-surface-1 p-space-3 md:p-space-4";
const AROUND = [5, 10, 0] as const;
const chip = (on: boolean) => cx("min-h-8 rounded-control border px-space-2", on ? "border-brand text-fg" : "border-subtle text-fg-muted");
const input = "min-h-8 rounded-control border border-subtle bg-surface-2 px-space-2 text-t13 text-fg";
/** The strike column stays in view while the table scrolls sideways (phone). */
const STICKY = "sticky left-0 z-10 whitespace-nowrap px-space-2 py-space-1 font-tabular";
const tone = (v: number | null) => (v === null || v === 0 ? "text-fg-muted" : v > 0 ? "text-up" : "text-down");

type Cover = { status: "loading" } | { status: "off" } | { status: "error" } | { status: "ok"; days: CoverageDay[] };

export default function OiHistoryV2({ underlying }: { underlying: string }) {
  const [cover, setCover] = useState<Cover>({ status: "loading" });
  const [day, setDay] = useState("");
  const [expiries, setExpiries] = useState<string[]>([]);
  const [expiry, setExpiry] = useState("");
  const dayId = useId(), expiryId = useId();

  useEffect(() => {
    const stop = new AbortController();
    setCover({ status: "loading" }); setDay(""); setExpiries([]); setExpiry("");
    historyApi.coverage(underlying, stop.signal).then((r) => {
      const days = r.days.filter((d) => d.underlying === underlying);
      setCover({ status: "ok", days });
      if (days[0]) setDay(days[0].day);
    }).catch((e: unknown) => {
      if (stop.signal.aborted) return;
      setCover(e instanceof ApiError && e.status === 404 ? { status: "off" } : { status: "error" });
    });
    return () => stop.abort();
  }, [underlying]);

  useEffect(() => {
    if (!day) return undefined;
    const stop = new AbortController();
    historyApi.expiries(underlying, `${day}T23:59:00`, stop.signal).then((r) => {
      const list = r.expiries.map((e) => e.expiry);
      setExpiries(list);
      setExpiry((cur) => (list.includes(cur) ? cur : list[0] ?? ""));
    }).catch(() => { if (!stop.signal.aborted) { setExpiries([]); setExpiry(""); } });
    return () => stop.abort();
  }, [underlying, day]);

  if (cover.status === "loading") return null;
  if (cover.status !== "ok" || cover.days.length === 0) {
    return (
      <section aria-label={`${underlying} OI over time`} className={CARD} data-oi-history="none">
        <h3 className="mb-space-1 text-t15 font-semibold text-fg">OI change between two times</h3>
        <p className="text-t13 text-fg-muted">
          {cover.status === "error" ? "Your recorded option chains could not be read right now."
            : `No recorded option chains for ${underlying} yet. They are kept from your own broker's readings while the market is open.`}
        </p>
      </section>
    );
  }
  const today = cover.days.find((d) => d.day === day) ?? cover.days[0];
  return (
    <div className="space-y-space-3" data-oi-history={underlying}>
      <div className="flex flex-wrap items-end gap-space-2 text-t13">
        <div className="flex min-w-[9rem] flex-col gap-space-1">
          <label htmlFor={dayId} className="text-t12 text-fg-muted">Day</label>
          <Combobox id={dayId} label="Day" value={day} onChange={setDay} dataAttrs={{ "data-oi-history-day": "" }}
            options={cover.days.map((d) => ({ value: d.day, label: formatDateIST(d.day, { year: false }) }))} />
        </div>
        <div className="flex min-w-[9rem] flex-col gap-space-1">
          <label htmlFor={expiryId} className="text-t12 text-fg-muted">Expiry</label>
          <Combobox id={expiryId} label="Expiry" value={expiry} onChange={setExpiry} disabled={expiries.length === 0} placeholder={EMPTY}
            dataAttrs={{ "data-oi-history-expiry": "" }} options={expiries.map((x) => ({ value: x, label: formatDateIST(x, { year: false }) }))} />
        </div>
        <span className="pb-space-1 text-t12 text-fg-muted">Stored {hhmm(today.first_slot)}–{hhmm(today.last_slot)} IST</span>
      </div>
      <RangeCard underlying={underlying} day={today} expiry={expiry} />
      <ReplayCard underlying={underlying} day={today.day} expiry={expiry} />
    </div>
  );
}

type Read<T> = { status: "idle" } | { status: "loading" } | { status: "error"; text: string } | { status: "ok"; value: T };
const failText = (e: unknown) => (e instanceof ApiError && e.status === 422 ? String(e.message) : "Could not read the stored chains right now.");

function RangeCard({ underlying, day, expiry }: { underlying: string; day: CoverageDay; expiry: string }) {
  const [from, setFrom] = useState(hhmm(day.first_slot));
  const [to, setTo] = useState(hhmm(day.last_slot));
  const [around, setAround] = useState<(typeof AROUND)[number]>(10);
  const [read, setRead] = useState<Read<RangeReply>>({ status: "idle" });
  useEffect(() => { setFrom(hhmm(day.first_slot)); setTo(hhmm(day.last_slot)); setRead({ status: "idle" }); }, [day.day, day.first_slot, day.last_slot]);
  useEffect(() => { setRead({ status: "idle" }); }, [expiry]);
  const start = pickerTime(day.day, from), end = pickerTime(day.day, to);
  const show = () => {
    if (!start || !end || !expiry) return;
    setRead({ status: "loading" });
    historyApi.range(underlying, expiry, start, end).then((value) => setRead({ status: "ok", value }), (e) => setRead({ status: "error", text: failText(e) }));
  };
  const reply = read.status === "ok" ? read.value : null;
  const rows = reply ? rowsAround(reply.rows, reply.atm, around) : [];
  const note = rangeNote(reply);
  const cell = "whitespace-nowrap px-space-2 py-space-1 text-right font-tabular";
  return (
    <section aria-label={`${underlying} OI change between two times`} className={CARD} data-oi-range>
      <h3 className="mb-space-2 text-t15 font-semibold text-fg">OI change between two times</h3>
      <div className="flex flex-wrap items-end gap-space-2 text-t13">
        <label className="flex flex-col gap-space-1"><span className="text-t12 text-fg-muted">From (IST)</span>
          <input type="time" className={input} value={from} min={hhmm(day.first_slot)} max={hhmm(day.last_slot)} onChange={(e) => setFrom(e.target.value)} data-oi-range-from /></label>
        <label className="flex flex-col gap-space-1"><span className="text-t12 text-fg-muted">To (IST)</span>
          <input type="time" className={input} value={to} min={hhmm(day.first_slot)} max={hhmm(day.last_slot)} onChange={(e) => setTo(e.target.value)} data-oi-range-to /></label>
        <button type="button" className={cx(chip(true), "font-medium")} onClick={show} disabled={!start || !end || !expiry || read.status === "loading"} data-oi-range-show>
          {read.status === "loading" ? "Reading…" : "Show"}
        </button>
      </div>
      {read.status === "error" && <p className="mt-space-2 text-t13 text-warn">{read.text}</p>}
      {note && <p className="mt-space-2 text-t13 text-fg-muted" data-oi-range-note>{note}</p>}
      {reply && !note && (
        <div className="mt-space-2 space-y-space-2">
          <div className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
            <span>{hhmm(reply.from?.captured_at)} → {hhmm(reply.to?.captured_at)} IST{[usedNote(reply.start, reply.from), usedNote(reply.end, reply.to)].some(Boolean) ? " (newest readings at or before the times picked)" : ""}</span>
            {reply.to?.spot != null && <span>{underlying} {formatPrice(reply.to.spot)}</span>}
            {reply.totals && <span>Total: call <b className={tone(reply.totals.ce_change)}>{formatLakhsChange(reply.totals.ce_change)}</b> · put <b className={tone(reply.totals.pe_change)}>{formatLakhsChange(reply.totals.pe_change)}</b></span>}
            <span className="ml-auto inline-flex gap-space-1">
              {AROUND.map((a) => <button key={a} type="button" aria-pressed={around === a} className={chip(around === a)} onClick={() => setAround(a)}>{a ? `±${a}` : "All"}</button>)}
            </span>
          </div>
          <div className="overflow-x-auto rounded-panel border border-subtle">
            <table className="w-full min-w-[22rem] border-collapse text-t13" data-oi-range-table>
              <thead>
                <tr className="border-b border-subtle text-t12 text-fg-muted">
                  <th className={cx(STICKY, "bg-surface-1 text-left font-medium")}>Strike</th>
                  <th className={cx(cell, "font-medium")}>Call chg</th>
                  <th className={cx(cell, "font-medium")}>Put chg</th>
                  <th className={cx(cell, "font-medium")}>Put − call</th>
                  <th className={cx(cell, "font-medium")}>Call OI</th>
                  <th className={cx(cell, "font-medium")}>Put OI</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.strike} data-oi-range-strike={r.strike} className={cx("border-b border-subtle last:border-b-0", r.strike === reply.atm && "bg-surface-2")}>
                    <td className={cx(STICKY, "font-medium text-fg", r.strike === reply.atm ? "bg-surface-2" : "bg-surface-1")}>{strikeText(r.strike)}</td>
                    <td className={cx(cell, tone(r.ce_change))}>{formatLakhsChange(r.ce_change)}</td>
                    <td className={cx(cell, tone(r.pe_change))}>{formatLakhsChange(r.pe_change)}</td>
                    <td className={cx(cell, tone(r.diff))}>{formatLakhsChange(r.diff)}</td>
                    <td className={cx(cell, "text-fg-muted")}>{formatLakhs(r.ce_to)}</td>
                    <td className={cx(cell, "text-fg-muted")}>{formatLakhs(r.pe_to)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-t12 text-fg-muted sm:hidden" data-scroll-hint>Swipe the table sideways for call and put OI →</p>
        </div>
      )}
    </section>
  );
}

function ReplayCard({ underlying, day, expiry }: { underlying: string; day: string; expiry: string }) {
  const [read, setRead] = useState<Read<ReplayReply>>({ status: "idle" });
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  useEffect(() => { setRead({ status: "idle" }); setPlaying(false); setIndex(0); }, [underlying, day, expiry]);
  const reply = read.status === "ok" ? read.value : null;
  const count = reply?.frames.length ?? 0;
  useEffect(() => {
    if (!playing || count === 0) return undefined;
    const id = window.setTimeout(() => {
      const next = nextFrame(index, count);
      if (next === null) setPlaying(false); else setIndex(next);
    }, FRAME_MS / speed);
    return () => window.clearTimeout(id);
  }, [playing, index, count, speed]);
  const load = () => {
    if (!expiry) return;
    setRead({ status: "loading" });
    historyApi.replay(underlying, expiry, day, 10).then((value) => { setRead({ status: "ok", value }); setIndex(0); }, (e) => setRead({ status: "error", text: failText(e) }));
  };
  const rows = useMemo(() => (reply ? replayRows(reply, clampFrame(index, count)) : []), [reply, index, count]);
  const scale = useMemo(() => (reply ? replayScale(reply) : 0), [reply]);
  const frame = reply?.frames[clampFrame(index, count)];
  const bar = (v: number | null) => (scale > 0 && v !== null ? `${Math.max(2, (v / scale) * 100)}%` : "0%");
  const cell = "whitespace-nowrap px-space-2 py-space-1 text-right font-tabular";
  return (
    <section aria-label={`${underlying} OI replay`} className={CARD} data-oi-replay>
      <div className="mb-space-2 flex flex-wrap items-center gap-space-2">
        <h3 className="text-t15 font-semibold text-fg">Replay</h3>
        {read.status !== "ok" && (
          <button type="button" className={chip(true)} onClick={load} disabled={!expiry || read.status === "loading"} data-oi-replay-load>
            {read.status === "loading" ? "Reading…" : "Load the day's readings"}
          </button>
        )}
      </div>
      {read.status === "error" && <p className="text-t13 text-warn">{read.text}</p>}
      {reply && count === 0 && <p className="text-t13 text-fg-muted" data-oi-replay-none>No stored readings for this day and expiry.</p>}
      {reply && frame && (
        <div className="space-y-space-2">
          <div className="flex flex-wrap items-center gap-space-2 text-t13">
            <button type="button" className={chip(playing)} aria-pressed={playing} data-oi-replay-play
              onClick={() => { if (!playing && nextFrame(index, count) === null) setIndex(0); setPlaying(!playing); }}>{playing ? "Pause" : "Play"}</button>
            <span className="inline-flex gap-space-1">
              {SPEEDS.map((s) => <button key={s} type="button" aria-pressed={speed === s} className={chip(speed === s)} onClick={() => setSpeed(s)}>{s}×</button>)}
            </span>
            <span className="font-tabular font-medium text-fg" data-oi-replay-time>{hhmm(frame.captured_at)} IST</span>
            {frame.spot !== null && <span className="text-fg-muted">{underlying} {formatPrice(frame.spot)}</span>}
            <span className="text-t12 text-fg-muted">{clampFrame(index, count) + 1} / {count}</span>
          </div>
          <input type="range" min={0} max={Math.max(0, count - 1)} step={1} value={clampFrame(index, count)} aria-label="Reading time"
            className="w-full" onChange={(e) => { setPlaying(false); setIndex(clampFrame(Number(e.target.value), count)); }} data-oi-replay-slider />
          <div className="flex justify-between text-t12 text-fg-muted"><span>{hhmm(reply.frames[0].captured_at)}</span><span>{hhmm(reply.frames[count - 1].captured_at)}</span></div>
          <div className="overflow-x-auto rounded-panel border border-subtle">
            <table className="w-full min-w-[22rem] border-collapse text-t13" data-oi-replay-table>
              <thead>
                <tr className="border-b border-subtle text-t12 text-fg-muted">
                  <th className={cx(STICKY, "bg-surface-1 text-left font-medium")}>Strike</th>
                  <th className={cx(cell, "font-medium")}>Call OI</th>
                  <th className={cx(cell, "font-medium")}>Call chg</th>
                  <th className={cx(cell, "font-medium")}>Put OI</th>
                  <th className={cx(cell, "font-medium")}>Put chg</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.strike} className="border-b border-subtle last:border-b-0" data-oi-replay-strike={r.strike}>
                    <td className={cx(STICKY, "bg-surface-1 font-medium text-fg")}>{strikeText(r.strike)}</td>
                    <BarCell value={r.ce} width={bar(r.ce)} fill="bg-oi-call" />
                    <td className={cx(cell, tone(r.ceChange))}>{formatLakhsChange(r.ceChange)}</td>
                    <BarCell value={r.pe} width={bar(r.pe)} fill="bg-oi-put" />
                    <td className={cx(cell, tone(r.peChange))}>{formatLakhsChange(r.peChange)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-t12 text-fg-muted sm:hidden" data-scroll-hint>Swipe the table sideways for more →</p>
          <p className="text-t12 text-fg-muted">Change since the day's first reading ({hhmm(reply.frames[0].captured_at)}). Strikes near each reading's ATM.</p>
        </div>
      )}
    </section>
  );
}

/** OI as a readable number with a thin bar UNDER it (never behind the text), scaled to the day's largest OI. */
function BarCell({ value, width, fill }: { value: number | null; width: string; fill: string }) {
  return (
    <td className="min-w-[5.5rem] px-space-2 py-space-1 text-right font-tabular text-fg">
      <span className="block">{formatLakhs(value)}</span>
      <span aria-hidden className="mt-0.5 block h-1 w-full rounded bg-surface-2">
        <span className={cx("ml-auto block h-1 rounded", fill)} style={{ width }} />
      </span>
    </td>
  );
}

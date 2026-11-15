import { Check, Copy, KeyRound, Plug, Power, Server, ShieldCheck, Trash2, Wrench } from "lucide-react";
import { useCallback, useEffect, useId, useState, type ReactNode } from "react";
import { errorSentence } from "../api/errors";
import { Button } from "../components/primitives/Button";
import { CARD, LINK_SECONDARY, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { SETTINGS_PATH } from "../settings/paths";
import { deskApi, type SupportContact } from "../support/desk";
import { SupportContactV2 } from "../support/HelpCentreV2";
import { PageTitleV2 } from "../trading/PartsV2";
import { AdvancedFold } from "../ui/AdvancedFold";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Details } from "../ui/Details";
import { InPlaceLink } from "../inplace/InPlaceLink";
import { formatDateTimeIST } from "../ui/format";
import {
  BROKER_KEYS_LINE, DEFAULT_SETTINGS, INSTALL_LINE, KILL_OFF_LINE, KILL_ON_LINE, OFFLINE_MINUTES_MAX, OFFLINE_MINUTES_MIN, ONLINE_WORD,
  PAIRING_MINUTES, PAIR_AGAIN_LINE, RUN_WORD, STATUS_LINE, activeDevices, ago, clock, loginLine, money, onlineState, parseMinutes, runnerApi,
  secondsLeft, type MirrorAnswer, type PairingCode, type RunnerDevice, type RunnerSettings,
} from "./model";

/**
 * Runner S7: "My runner" (`/runner`, v2 only, flag `user_runner`). His own runner on his own VPS:
 * - none paired: the plain steps (VPS -> install line -> pairing code -> static IP at Angel One -> broker keys -> done)
 *   and the "We set it up for you" card (the existing support contact; no payment here);
 * - paired: one card per runner (online / offline and last seen, version, broker login, HIS kill switch, Remove), his
 *   two link settings (copies to ATP, offline alert), and the copies his runner sent (read-only, "From your runner").
 * ATP never starts, stops or changes his runner (rule R-1): the kill switch is changed on the runner, or with his own
 * button once the link relays his command (S4, `kill_relay`). Exits are never blocked (ADR-0004).
 */

const REFRESH_MS = 30_000;
const BTN = "px-space-4";

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return now;
}

/** A command he pastes on his VPS, with a Copy button. */
export function CommandLine({ line, label }: { line: string; label: string }) {
  const [copied, setCopied] = useState<null | "ok" | "no">(null);
  async function copy() {
    try { await navigator.clipboard.writeText(line); setCopied("ok"); } catch { setCopied("no"); }
    window.setTimeout(() => setCopied(null), 2000);
  }
  return (
    <div className="mt-space-2 flex flex-col gap-space-2 sm:flex-row sm:items-start" data-command={label}>
      <code className="min-w-0 flex-1 break-all rounded-control border border-border bg-surface-2 px-space-3 py-space-2 font-mono text-t13 text-fg">
        {line}
      </code>
      <Button size="v2" variant="outline" className={`${BTN} shrink-0`} onClick={() => void copy()} aria-label={`Copy: ${label}`}
        icon={copied === "ok" ? <Check size={16} aria-hidden /> : <Copy size={16} aria-hidden />}>
        {copied === "ok" ? "Copied" : copied === "no" ? "Copy by hand" : "Copy"}
      </Button>
    </div>
  );
}

function Card({ title, icon, children, testId, className = "" }: { title: ReactNode; icon?: ReactNode; children: ReactNode; testId?: string; className?: string }) {
  const id = useId();
  return (
    <section aria-labelledby={id} data-card={testId} className={`${CARD} p-space-4 md:p-space-5 ${className}`}>
      <h2 id={id} className="flex items-center gap-space-2 text-t15 font-semibold text-fg">{icon}{title}</h2>
      <div className="mt-space-3">{children}</div>
    </section>
  );
}

// --- pairing code -------------------------------------------------------------------------------------------------------

/** The pairing code view (no state of its own): the button, or the code with its countdown. */
export function PairingCodeView({ code, now, busy, error, onCreate }: {
  code: PairingCode | null; now: number; busy: boolean; error: string | null; onCreate: () => void;
}) {
  const left = code ? secondsLeft(code.expires_at, now) : 0;
  return (
    <div data-pairing>
      {code && left > 0 ? (
        <div className="rounded-control border border-brand/40 bg-brand/10 p-space-3" data-pairing-code>
          <p className="text-t13 text-fg-muted">Your pairing code (shown once)</p>
          <p className="mt-space-1 break-all font-mono text-t32 font-semibold tracking-widest text-fg" aria-live="polite">{code.code}</p>
          <p className="mt-space-1 text-t13 text-fg" data-countdown>
            Expires in <span className="font-semibold tabular-nums">{clock(left)}</span>
          </p>
          <p className="mt-space-1 text-t13 text-fg-muted">Type it on your VPS when the installer asks for it.</p>
        </div>
      ) : (
        <>
          {code && <p className="mb-space-2 text-t13 text-warn" data-code-expired>That code has expired. Create a new one.</p>}
          <Button size="v2" variant="primary" className={BTN} loading={busy} onClick={onCreate} icon={<KeyRound size={16} aria-hidden />}>
            Create pairing code
          </Button>
          <p className="mt-space-1 text-t13 text-fg-muted">Valid for {PAIRING_MINUTES} minutes, shown once.</p>
        </>
      )}
      {error && <p role="alert" className="mt-space-2 text-t13 text-warn">{error}</p>}
    </div>
  );
}

function PairingCodeBlock() {
  const [code, setCode] = useState<PairingCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const now = useNow(1000);
  async function create() {
    setBusy(true); setError(null);
    try { setCode(await runnerApi.createCode()); } catch (e) { setError(errorSentence(e)); } finally { setBusy(false); }
  }
  return <PairingCodeView code={code} now={now} busy={busy} error={error} onCreate={() => void create()} />;
}

// --- empty state --------------------------------------------------------------------------------------------------------

function Step({ n, title, children }: { n: number; title: string; children?: ReactNode }) {
  return (
    <li className="flex gap-space-3" data-step={n}>
      <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-surface-2 text-t13 font-semibold text-fg">{n}</span>
      <div className="min-w-0 flex-1">
        <p className="text-t14 font-semibold text-fg">{title}</p>
        {children && <div className="mt-space-1 text-t13 text-fg-muted">{children}</div>}
      </div>
    </li>
  );
}

function SetupSteps() {
  return (
    <Card title="Set up your runner" icon={<Server size={18} aria-hidden />} testId="runner-steps">
      <p className="text-t13 text-fg-muted">About 10 minutes. Your broker keys stay on your own VPS.</p>
      <ol className="mt-space-4 space-y-space-4">
        <Step n={1} title="Rent a small VPS with a static IP">Ubuntu 22.04 / 24.04 · 1 vCPU · 1 GB RAM · 10 GB disk</Step>
        <Step n={2} title="Paste this one line on your VPS">
          <CommandLine line={INSTALL_LINE} label="install line" />
        </Step>
        <Step n={3} title="Create a pairing code">
          <div className="mt-space-2"><PairingCodeBlock /></div>
        </Step>
        <Step n={4} title="Register your VPS IP at Angel One">
          Add the IP the installer prints to your SmartAPI app.
          <Details id="runner.static-ip" label="Why">
            <p>Angel One accepts orders only from the static IP registered in your own SmartAPI app. The primary IP can be changed once a week.</p>
          </Details>
        </Step>
        <Step n={5} title="Add your broker keys on the VPS">
          <CommandLine line={BROKER_KEYS_LINE} label="broker keys" />
        </Step>
        <Step n={6} title="Done">It shows here within a minute, in paper mode.</Step>
      </ol>
    </Card>
  );
}

function SetupServiceCard() {
  const [contact, setContact] = useState<SupportContact | null>(null);
  useEffect(() => {
    let live = true;
    deskApi.contact().then((c) => { if (live) setContact(c); }, () => { /* intentional: no contact line; the Help link stays */ });
    return () => { live = false; };
  }, []);
  return (
    <Card title="We set it up for you" icon={<Wrench size={18} aria-hidden />} testId="runner-service">
      <ul className="list-disc space-y-space-1 pl-space-5 text-t13 text-fg">
        <li>A paid setup session on a screen share</li>
        <li>You type your own keys and PIN - we never keep them</li>
        <li>The VPS, broker account and app key stay in your name</li>
      </ul>
      <div className="mt-space-3 flex flex-col gap-space-2">
        <SupportContactV2 contact={contact} />
        <InPlaceLink to={SETTINGS_PATH.help} className={`${LINK_SECONDARY} self-start`} data-service-request>Ask for a setup session</InPlaceLink>
      </div>
    </Card>
  );
}

// --- paired state -------------------------------------------------------------------------------------------------------

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-space-3 gap-y-space-1 border-t border-subtle py-space-2 first:border-t-0">
      <dt className="text-t13 text-fg-muted">{label}</dt>
      <dd className="min-w-0 text-right text-t14 text-fg">{children}</dd>
    </div>
  );
}

export function killStateText(on: boolean | null): string {
  return on === null ? "Not reported yet" : on ? "ON - no new entries; exits continue" : "OFF";
}

/** HIS kill switch (rule R-1): the state his runner reported; his own button only while the link relays his command
 * (S4 `kill_relay`), otherwise the command to type on his runner. */
export function KillSwitchView({ device, busy, note, onToggle }: { device: RunnerDevice; busy: boolean; note: string | null; onToggle: (on: boolean) => void }) {
  const on = device.kill_switch_on;
  return (
    <div className="border-t border-subtle pt-space-3" data-kill={on === null ? "unknown" : on ? "on" : "off"}>
      <div className="flex flex-wrap items-center justify-between gap-space-2">
        <p className="text-t14 text-fg">
          <span className="text-fg-muted">Kill switch: </span>
          <span className={on ? "font-semibold text-warn" : "font-semibold"} data-kill-state>{killStateText(on)}</span>
        </p>
        {device.kill_relay === true && (
          <Button size="v2" variant="outline" className={BTN} loading={busy} onClick={() => onToggle(!on)} icon={<Power size={16} aria-hidden />}
            data-kill-toggle>
            {on ? "Turn kill switch off" : "Turn kill switch on"}
          </Button>
        )}
      </div>
      {device.kill_relay !== true && (
        <div className="mt-space-1 text-t13 text-fg-muted" data-kill-fallback>
          <p>{on ? "Turn it off on your runner:" : "Turn it on on your runner:"}</p>
          <CommandLine line={on ? KILL_OFF_LINE : KILL_ON_LINE} label={on ? "kill switch off" : "kill switch on"} />
        </div>
      )}
      {note && <p role="status" className="mt-space-2 text-t13 text-fg-muted">{note}</p>}
    </div>
  );
}

function KillSwitch({ device, onChanged }: { device: RunnerDevice; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  async function relay(next: boolean) {
    setBusy(true); setNote(null);
    try { await runnerApi.setKill(device.device_id, next); setNote("Sent to your runner. It shows here after its next report."); onChanged(); }
    catch (e) { setNote(errorSentence(e)); }
    finally { setBusy(false); }
  }
  return <KillSwitchView device={device} busy={busy} note={note} onToggle={(v) => void relay(v)} />;
}

export function DeviceCard({ device, now, onRevoke, onChanged }: { device: RunnerDevice; now: number; onRevoke: () => void; onChanged: () => void }) {
  const state = onlineState(device);
  const seen = ago(device.last_heartbeat_at, now);
  const login = loginLine(device);
  const tone = state === "online" ? "text-up" : state === "offline" ? "text-down" : "text-fg-muted";
  return (
    <Card testId={`runner-${device.device_id}`} icon={<Server size={18} aria-hidden />}
      title={<span className="flex min-w-0 flex-wrap items-center gap-x-space-2"><span className="break-all">{device.name}</span>
        <span className={`inline-flex items-center gap-space-1 text-t13 font-semibold ${tone}`} data-online={state}>
          <span aria-hidden className={`h-2 w-2 rounded-full ${state === "online" ? "bg-up" : state === "offline" ? "bg-down" : "bg-surface-3"}`} />
          {ONLINE_WORD[state]}
        </span></span>}>
      <dl>
        <Row label="Last seen">{seen ?? "Not yet"}</Row>
        <Row label="Open positions">{device.open_positions ?? "-"}</Row>
        <Row label="State">{device.run_state ? RUN_WORD[device.run_state] : "-"}</Row>
        <Row label="Version">{device.version ?? "-"}</Row>
      </dl>
      <p className={`mt-space-2 text-t14 ${login.ok === false ? "text-warn" : "text-fg"}`} data-login>{login.text}</p>
      {state === "offline" && <p className="mt-space-1 text-t13 text-fg-muted">Check that your VPS is on and online: <span className="break-all font-mono">{STATUS_LINE}</span></p>}
      <div className="mt-space-3"><KillSwitch device={device} onChanged={onChanged} /></div>
      <div className="mt-space-3 flex flex-wrap items-center justify-between gap-space-2 border-t border-subtle pt-space-3">
        <Details id="runner.device" label="Advanced">
          <p>VPS IP it reports from: <span className="font-mono">{device.egress_ip ?? "-"}</span></p>
          <p>Paired: {device.paired_at ? formatDateTimeIST(device.paired_at) : "-"}</p>
          <p>Removing a runner here does not stop it: it keeps managing its exits on your VPS. To stop it, use your VPS.</p>
        </Details>
        <Button size="v2" variant="outline" className={BTN} onClick={onRevoke} icon={<Trash2 size={16} aria-hidden />} data-revoke>
          Remove runner
        </Button>
      </div>
    </Card>
  );
}

function Toggle({ on, label, onChange, busy }: { on: boolean; label: string; onChange: (on: boolean) => void; busy?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={busy} onClick={() => onChange(!on)}
      className="inline-flex min-h-11 shrink-0 items-center gap-space-2 rounded-control px-space-1 text-t13 font-medium text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-60 md:min-h-9">
      <span className={`relative h-6 w-11 rounded-full transition-colors ${on ? "bg-brand" : "bg-surface-3"}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-surface-1 shadow ring-1 ring-border transition-all ${on ? "left-[1.375rem]" : "left-0.5"}`} />
      </span>
      <span className={on ? "text-up" : undefined}>{on ? "On" : "Off"}</span>
    </button>
  );
}

export function SettingsCard({ settings, onSaved }: { settings: RunnerSettings; onSaved: (s: RunnerSettings) => void }) {
  const [minutes, setMinutes] = useState(String(settings.offline_after_minutes));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => { setMinutes(String(settings.offline_after_minutes)); }, [settings.offline_after_minutes]);
  const parsed = parseMinutes(minutes);

  async function save(next: RunnerSettings) {
    setBusy(true); setNote(null);
    try { await runnerApi.saveSettings(next); onSaved(next); setNote("Saved."); } catch (e) { setNote(errorSentence(e)); } finally { setBusy(false); }
  }
  return (
    <Card title="Link settings" icon={<Plug size={18} aria-hidden />} testId="runner-settings">
      <div className="flex flex-wrap items-center justify-between gap-space-2">
        <div className="min-w-0">
          <p className="text-t14 text-fg">Send copies to ATP</p>
          <p className="text-t13 text-fg-muted">Its orders and positions, read-only</p>
        </div>
        <Toggle on={settings.mirror_upload} label="Send copies to ATP" busy={busy} onChange={(v) => void save({ ...settings, mirror_upload: v })} />
      </div>
      <div className="mt-space-3 border-t border-subtle pt-space-3">
        <label htmlFor="runner-offline-minutes" className="text-t14 text-fg">Alert me when my runner is offline for</label>
        <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
          <input id="runner-offline-minutes" inputMode="numeric" value={minutes} onChange={(e) => { setMinutes(e.target.value); setNote(null); }}
            aria-invalid={parsed === null} aria-describedby="runner-offline-hint"
            className="h-11 w-20 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg tabular-nums focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:h-9" />
          <span className="text-t14 text-fg">minutes</span>
          <Button size="v2" variant="outline" className={BTN} disabled={parsed === null || parsed === settings.offline_after_minutes || busy}
            onClick={() => parsed !== null && void save({ ...settings, offline_after_minutes: parsed })}>Save</Button>
        </div>
        <p id="runner-offline-hint" className={`mt-space-1 text-t13 ${parsed === null ? "text-warn" : "text-fg-muted"}`}>
          {parsed === null ? `A whole number from ${OFFLINE_MINUTES_MIN} to ${OFFLINE_MINUTES_MAX}.` : "In the app's bell, and on your Telegram if it is connected."}
        </p>
      </div>
      {note && <p role="status" className="mt-space-2 text-t13 text-fg-muted">{note}</p>}
    </Card>
  );
}

const TH = "px-space-2 py-space-2 text-left text-t13 font-medium text-fg-muted";
const TD = "px-space-2 py-space-2 text-t14 text-fg";

function pnlClass(v: number | null): string {
  if (v === null || v === 0) return "text-fg";
  return v > 0 ? "text-up" : "text-down";
}

function MirrorCard({ device, mirrorOn }: { device: RunnerDevice; mirrorOn: boolean }) {
  const [data, setData] = useState<MirrorAnswer | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    runnerApi.mirror(device.device_id).then((d) => { if (live) { setData(d); setFailed(false); } }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [device.device_id, device.last_heartbeat_at]);
  return <MirrorView device={device} data={data} failed={failed} mirrorOn={mirrorOn} />;
}

/** The copies his runner sent (read-only): open positions and the latest orders. */
export function MirrorView({ device, data, failed, mirrorOn }: { device: RunnerDevice; data: MirrorAnswer | null; failed: boolean; mirrorOn: boolean }) {
  const open = (data?.positions ?? []).filter((p) => p.status === "OPEN");
  const orders = (data?.orders ?? []).slice(0, 10);
  return (
    <Card title={<>From your runner <span className="text-t13 font-normal text-fg-muted">· {device.name} · read-only</span></>} testId="runner-mirror">
      {!mirrorOn && <p className="mb-space-2 text-t13 text-fg-muted" data-mirror-off>Copies are off. Turn on "Send copies to ATP" to see them here.</p>}
      {failed ? <LoadFailedV2 what="your runner's copies" /> : !data ? <LoadingRowsV2 rows={2} /> : (
        <div className="space-y-space-4">
          <div>
            <h3 className="text-t14 font-semibold text-fg">Open positions <span className="text-fg-muted">({open.length})</span></h3>
            {open.length === 0 ? <p className="mt-space-1 text-t13 text-fg-muted">None.</p> : (
              <div className="mt-space-1 overflow-x-auto">
                <table className="w-full min-w-[320px]" data-mirror-positions>
                  <thead><tr><th className={TH}>Symbol</th><th className={`${TH} text-right`}>Qty</th><th className={`${TH} text-right`}>Avg</th><th className={`${TH} text-right`}>P&amp;L</th></tr></thead>
                  <tbody>
                    {open.map((p, i) => (
                      <tr key={p.ref ?? i} className="border-t border-subtle">
                        <td className={TD}>{p.symbol} <span className="text-t12 text-fg-muted">{p.mode === "PAPER" ? "Paper" : "Live"}</span></td>
                        <td className={`${TD} text-right tabular-nums`}>{p.quantity}</td>
                        <td className={`${TD} text-right tabular-nums`}>{money(p.average_price)}</td>
                        <td className={`${TD} text-right tabular-nums ${pnlClass(p.unrealised_pnl)}`}>{money(p.unrealised_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <div>
            <h3 className="text-t14 font-semibold text-fg">Latest orders <span className="text-fg-muted">({orders.length})</span></h3>
            {orders.length === 0 ? <p className="mt-space-1 text-t13 text-fg-muted">None.</p> : (
              <div className="mt-space-1 overflow-x-auto">
                <table className="w-full min-w-[320px]" data-mirror-orders>
                  <thead><tr><th className={TH}>Symbol</th><th className={TH}>Side</th><th className={`${TH} text-right`}>Qty</th><th className={TH}>Status</th></tr></thead>
                  <tbody>
                    {orders.map((o, i) => (
                      <tr key={o.ref ?? i} className="border-t border-subtle">
                        <td className={TD}>{o.symbol} <span className="text-t12 text-fg-muted">{o.mode === "PAPER" ? "Paper" : "Live"}</span></td>
                        <td className={TD}>{o.side === "BUY" ? "Buy" : "Sell"}</td>
                        <td className={`${TD} text-right tabular-nums`}>{o.filled_quantity ?? 0}/{o.quantity}</td>
                        <td className={`${TD} text-t13`}>{o.status.charAt(0) + o.status.slice(1).toLowerCase().replace(/_/g, " ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

// --- the page -----------------------------------------------------------------------------------------------------------

export const REVOKE_TITLE = "Remove this runner?";
export const revokedNote = (name: string) => `${name} was removed from your account.`;

export interface RunnerBodyProps {
  devices: RunnerDevice[] | null;
  settings: RunnerSettings;
  failed: boolean;
  now: number;
  onRevoke: (d: RunnerDevice) => void;
  onChanged: () => void;
  onSettings: (s: RunnerSettings) => void;
  /** The copies per runner (the page fills them; a test passes them). */
  mirror?: (d: RunnerDevice) => ReactNode;
}

/** The page's content for what was read: loading, failed, the set-up steps, or his runners. */
export function RunnerBody({ devices, settings, failed, now, onRevoke, onChanged, onSettings, mirror }: RunnerBodyProps) {
  if (failed && !devices) return <Card title="My runner"><LoadFailedV2 what="your runner" /></Card>;
  if (!devices) return <Card title="My runner"><LoadingRowsV2 rows={3} /></Card>;
  const active = activeDevices(devices);
  const removed = devices.length - active.length;
  if (active.length === 0) {
    return (
      <div className="grid gap-space-4 lg:grid-cols-[minmax(0,1fr)_300px]" data-runner-empty>
        <SetupSteps />
        <SetupServiceCard />
      </div>
    );
  }
  return (
    <div className="space-y-space-4" data-runner-paired>
      <div className="grid gap-space-4 md:grid-cols-2">
        {active.map((d) => <DeviceCard key={d.device_id} device={d} now={now} onRevoke={() => onRevoke(d)} onChanged={onChanged} />)}
      </div>
      <SettingsCard settings={settings} onSaved={onSettings} />
      {active.map((d) => <div key={d.device_id}>{mirror ? mirror(d) : <MirrorCard device={d} mirrorOn={settings.mirror_upload} />}</div>)}
      <AdvancedFold id="runner-advanced" title="Advanced">
        <div className="space-y-space-3 p-space-3 text-t13 text-fg-muted">
          <div><p className="text-fg">Pair another runner</p><PairingCodeBlock /></div>
          <div><p className="text-fg">Pair a runner that is already installed</p><CommandLine line={PAIR_AGAIN_LINE} label="pair again" /></div>
          <div><p className="text-fg">Check your runner on the VPS</p><CommandLine line={STATUS_LINE} label="status" /></div>
          <p className="flex items-center gap-space-1"><ShieldCheck size={14} aria-hidden /> Only you turn the kill switch on or off. Exits always run.</p>
          {removed > 0 && <p data-removed>Removed runners: {removed}</p>}
        </div>
      </AdvancedFold>
    </div>
  );
}

export default function MyRunnerPageV2() {
  const [devices, setDevices] = useState<RunnerDevice[] | null>(null);
  const [settings, setSettings] = useState<RunnerSettings>(DEFAULT_SETTINGS);
  const [failed, setFailed] = useState(false);
  const [revoking, setRevoking] = useState<RunnerDevice | null>(null);
  const [revokeBusy, setRevokeBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const now = useNow(REFRESH_MS);

  const load = useCallback(() => {
    runnerApi.devices().then((a) => { setDevices(a.devices); setSettings({ ...DEFAULT_SETTINGS, ...a.settings }); setFailed(false); },
      () => setFailed(true));
  }, []);
  useEffect(() => {
    load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => window.clearInterval(t);
  }, [load]);

  async function revoke() {
    if (!revoking) return;
    setRevokeBusy(true);
    try { await runnerApi.revoke(revoking.device_id); setNote(revokedNote(revoking.name)); setRevoking(null); load(); }
    catch (e) { setNote(errorSentence(e)); setRevoking(null); }
    finally { setRevokeBusy(false); }
  }

  return (
    <div className="mx-auto w-full max-w-[880px] space-y-space-4" data-page="my-runner">
      <PageTitleV2 title="My runner" subtitle="Your own runner on your own VPS: your broker keys and your static IP stay with you." />
      {note && <p role="status" className="text-t13 text-fg-muted">{note}</p>}
      <RunnerBody devices={devices} settings={settings} failed={failed} now={now} onRevoke={setRevoking} onChanged={load} onSettings={setSettings} />
      <ConfirmDialog open={revoking !== null} onOpenChange={(o) => { if (!o) setRevoking(null); }} title={REVOKE_TITLE}
        consequence={`Reports from ${revoking?.name ?? "this runner"} stop. Exits keep running on your VPS.`}
        confirmLabel="Remove runner" onConfirm={() => void revoke()} busy={revokeBusy} note="You can pair it again with a new code." />
    </div>
  );
}

import { SETTINGS_PATH } from "../settings/paths";
import * as RD from "@radix-ui/react-dialog";
import { AlertTriangle, CheckCircle2, KeyRound, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type ClipboardEvent } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { ApiError, friendlyError } from "../api/errors";
import { CARD, LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { AI_CONNECTION_CHANGED_EVENT, type AiConnection, type AiLimits } from "./connect";
import { plainError } from "./errors";
import { FREE_TIER_NOTICE } from "./guides";
import KeyGuidesV2 from "./KeyGuidesV2";
import { MAY_TRAIN_ON_FREE_TIER, PICKER_ORDER, PROVIDER_FACTS, PROVIDER_LABELS, cleanKey, detectProvider, refusalFor, type AiProviderName,
  type Detection } from "./keyFormats";

/**
 * Settings redesign part A ("Connect AI in one paste", behind `ui_v2` + `ai_one_key`): ONE key for every AI feature.
 * - Paste: the key is trimmed and its provider read from its format (src/aiKeys/keyFormats.ts); an unknown format gets a
 *   small picker (Google Gemini, Claude, OpenAI, Mistral, Z.ai GLM - MO2 part A); a sign-in token, an admin key or a
 *   service ATP cannot use gets one line.
 * - Before the first save: the consent line and its checkbox. A free tier that may train on data gets a one-line warning.
 *   MO2 part A: a free-tier provider also shows the privacy notice, every provider its one-line data term, and a
 *   provider that does not read charts says so before it is saved (and once connected).
 * - Then it saves (encrypted) and tests (one text call and one tiny image) by itself - there is no Save button, no model,
 *   no provider dropdown and no on/off switch here. "Connected · Claude · key …a9F2" and Disconnect.
 * - A failed test keeps the key: the plain sentence, Fix (paste again) and Remove.
 * - Limits: "AI this month: ₹X of your ₹Y limit" with a bar, and the daily / monthly limits in rupees.
 * - "Get a free key" opens the key guides in a drawer, free Google Gemini first.
 * The key is cleared from the box once sent and never shown again (only its last four characters).
 */

export const CONSENT_LINE = (label: string) => `Your questions and chart images are sent to ${label} to answer you. Never your broker login or passwords.`;
const INTRO = "Paste one AI key and every AI feature works: the Copilot, chart analysis and the idea tools.";
const FREE_TIER = (label: string) => `Free keys from ${label} may be used to train its models. Do not paste anything private into the chat.`;

type Step = "idle" | "busy";

export interface ConnectAiViewProps {
  conn: AiConnection;
  raw: string;
  picked: AiProviderName | null;
  consent: boolean;
  step: Step;
  notice: string | null;
  fixing: boolean;
  onRaw: (raw: string) => void;
  onPick: (p: AiProviderName) => void;
  onConsent: (on: boolean) => void;
  onDisconnect: () => void;
  onFix: () => void;
  onGuides: () => void;
  onAcknowledge: (kind: "consent" | "paid_plan") => void;
  onCopy: () => void;
  onLimits: (body: { daily_inr?: number; monthly_inr?: number }) => void;
}

/** The provider the next save goes to: the key's own format, else the picker. */
export function chosenProvider(found: Detection, picked: AiProviderName | null): AiProviderName | null {
  if (found.kind === "provider") return found.provider;
  if (found.kind === "unknown") return picked;
  return null;
}

/** True when the panel may save and test by itself now. */
export function readyToConnect(raw: string, picked: AiProviderName | null, consent: boolean, step: Step): boolean {
  return step === "idle" && consent && cleanKey(raw) !== "" && chosenProvider(detectProvider(raw), picked) !== null;
}

const CHECK = "mt-0.5 h-5 w-5 shrink-0 accent-brand";
const LINE_BTN = "inline-flex min-h-11 items-center rounded-control px-space-2 text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9";

export function ConnectAiView(p: ConnectAiViewProps) {
  const { conn } = p;
  const showPaste = !conn.connected || p.fixing;
  return (
    <section aria-labelledby="connect-ai-title" className={`${CARD} space-y-space-4 p-space-4 text-fg md:p-space-5`}
      data-connect-ai={conn.connected ? (conn.ok === false ? "failed" : "connected") : "not-connected"}>
      <div className="flex items-start gap-space-3">
        <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-brand/10 text-brand"><Sparkles size={20} /></span>
        <div className="min-w-0">
          <h2 id="connect-ai-title" className="text-t15 font-semibold text-fg">Connect AI</h2>
          {!conn.connected && <p className="mt-space-1 text-t14 text-fg-muted">{INTRO}</p>}
        </div>
      </div>
      {conn.connected && <Connected {...p} />}
      {showPaste && <Paste {...p} />}
      {conn.connected && <Limits limits={conn.limits} onLimits={p.onLimits} />}
    </section>
  );
}

function Connected({ conn, step, fixing, onDisconnect, onFix, onAcknowledge }: ConnectAiViewProps) {
  const failed = conn.ok === false;
  return (
    <div className="space-y-space-3">
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <p role="status" className={`inline-flex min-w-0 items-center gap-space-2 text-t14 font-semibold ${failed ? "text-fg" : "text-up"}`} data-connected-line>
          {failed ? <AlertTriangle size={18} aria-hidden className="shrink-0 text-warn" /> : <CheckCircle2 size={18} aria-hidden className="shrink-0" />}
          <span>{failed ? "Saved, but not working" : "Connected"} · {conn.label} · <span className="whitespace-nowrap">key …{conn.key_end}</span></span>
        </p>
        <button type="button" onClick={onDisconnect} disabled={step === "busy"} className={`${LINK_SECONDARY} disabled:opacity-50`} data-action="disconnect">
          {failed ? "Remove" : "Disconnect"}
        </button>
      </div>
      {failed && (
        <div role="alert" className="flex flex-col gap-space-2 rounded-control border border-subtle bg-surface-2 p-space-3 sm:flex-row sm:items-center sm:justify-between" data-test-error>
          <p className="text-t14 text-fg">{conn.error}</p>
          {!fixing && <button type="button" onClick={onFix} className={LINK_PRIMARY} data-action="fix">Fix</button>}
        </div>
      )}
      {conn.consent && !conn.consent.accepted && (
        <label className="flex items-start gap-space-2 text-t14 text-fg" data-consent-again>
          <input type="checkbox" className={CHECK} onChange={(e) => { if (e.target.checked) onAcknowledge("consent"); }} />
          <span>{conn.consent.text}</span>
        </label>
      )}
      {conn.may_train && conn.free_tier_warning && <p className="text-t13 text-warn" data-free-tier>{conn.free_tier_warning}</p>}
      {conn.privacy && <p className="text-t13 text-fg-muted" data-privacy>{conn.privacy}</p>}
      {conn.vision === false && <p className="text-t13 text-fg-muted" data-no-vision>{plainError("no_vision")}</p>}
      {conn.may_train && !conn.paid_plan && (
        <label className="flex items-start gap-space-2 text-t14 text-fg" data-paid-plan>
          <input type="checkbox" className={CHECK} onChange={(e) => { if (e.target.checked) onAcknowledge("paid_plan"); }} />
          <span>{conn.paid_plan_text ?? "My key is on a paid plan."}</span>
        </label>
      )}
    </div>
  );
}

function Paste({ conn, raw, picked, consent, step, notice, onRaw, onPick, onConsent, onGuides, onCopy }: ConnectAiViewProps) {
  const fieldId = useId();
  const noteId = useId();
  const found = detectProvider(raw);
  const provider = chosenProvider(found, picked);
  const refusal = refusalFor(found);
  const label = provider ? PROVIDER_LABELS[provider] : null;
  const busy = step === "busy";
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData?.getData("text");
    if (text) { e.preventDefault(); onRaw(cleanKey(text)); }
  };
  const copyLabel = conn.copy_tenant_key?.label ?? null;
  return (
    <div className="space-y-space-3" data-paste>
      <div className="space-y-space-2">
        <label htmlFor={fieldId} className="block text-t14 font-semibold text-fg">Paste your AI key</label>
        <input id={fieldId} type="password" autoComplete="off" spellCheck={false} autoCapitalize="off" value={raw} disabled={busy}
          onChange={(e) => onRaw(e.target.value)} onPaste={onPaste} aria-describedby={noteId} placeholder="Paste the key here"
          className="h-11 w-full min-w-0 rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand disabled:opacity-60" />
      </div>
      <div id={noteId} aria-live="polite" className="space-y-space-2">
        {found.kind === "provider" && label && <p className="text-t13 text-fg-muted" data-detected={provider}>This is {/^[AEIOU]/i.test(label) ? "an" : "a"} {label} key.</p>}
        {refusal && <p className="text-t13 text-warn" data-key-refused>{refusal}</p>}
        {found.kind === "unknown" && (
          <fieldset className="space-y-space-2" data-picker>
            <legend className="text-t13 text-fg">Which service is this key from?</legend>
            <div className="flex flex-wrap gap-space-2">
              {PICKER_ORDER.map((p) => (
                <label key={p} className={`inline-flex min-h-11 cursor-pointer items-center gap-space-2 rounded-pill border px-space-3 text-t14 md:min-h-9 ${picked === p ? "border-brand bg-brand/10 text-fg" : "border-border bg-surface-1 text-fg"}`}>
                  <input type="radio" name={`${fieldId}-provider`} className="accent-brand" checked={picked === p} onChange={() => onPick(p)} />
                  {PROVIDER_LABELS[p]}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        {label && (
          <label className="flex items-start gap-space-2 text-t14 text-fg" data-consent>
            <input type="checkbox" className={CHECK} checked={consent} disabled={busy} onChange={(e) => onConsent(e.target.checked)} />
            <span>{CONSENT_LINE(label)}</span>
          </label>
        )}
        {provider && MAY_TRAIN_ON_FREE_TIER[provider] && <p className="text-t13 text-warn" data-free-tier>{FREE_TIER(PROVIDER_LABELS[provider])}</p>}
        {provider && <ProviderTerms provider={provider} />}
        {busy && <p role="status" className="text-t13 text-fg-muted" data-busy>Connecting and testing your key…</p>}
        {notice && <p role="alert" className="text-t13 text-fg" data-notice>{notice}</p>}
      </div>
      {copyLabel && !raw && (
        <div className="space-y-space-2 rounded-control border border-subtle bg-surface-2 p-space-3" data-copy-offer>
          <p className="text-t14 text-fg">Your organisation already has the {copyLabel} key you entered.</p>
          <label className="flex items-start gap-space-2 text-t14 text-fg">
            <input type="checkbox" className={CHECK} checked={consent} disabled={busy} onChange={(e) => onConsent(e.target.checked)} />
            <span>{CONSENT_LINE(copyLabel)}</span>
          </label>
          <button type="button" onClick={onCopy} disabled={!consent || busy} className={`${LINK_SECONDARY} disabled:opacity-50`} data-action="copy-tenant-key">
            Use this key for your account too
          </button>
        </div>
      )}
      <button type="button" onClick={onGuides} className={LINE_BTN} data-action="get-free-key">
        <KeyRound size={16} aria-hidden className="mr-space-1" /> Get a free key
      </button>
    </div>
  );
}

/** MO2 part A: before a key is saved - the privacy notice for a free tier, the provider's data term (a provider that may
 * train already has its own warning line above), and whether it reads charts. */
export function ProviderTerms({ provider }: { provider: AiProviderName }) {
  const facts = PROVIDER_FACTS[provider];
  return (
    <div className="space-y-space-1 text-t13 text-fg-muted" data-provider-terms={provider}>
      {facts.free_tier && <p data-privacy-notice>{FREE_TIER_NOTICE}</p>}
      {!facts.may_train && <p data-privacy>{facts.privacy}</p>}
      {!facts.vision && <p data-no-vision>{plainError("no_vision")}</p>}
    </div>
  );
}

function Limits({ limits, onLimits }: { limits: AiLimits; onLimits: ConnectAiViewProps["onLimits"] }) {
  const dayId = useId();
  const monthId = useId();
  const pct = limits.monthly_cap_inr > 0 ? Math.min(100, Math.round((limits.month_inr / limits.monthly_cap_inr) * 100)) : 100;
  const commit = (field: "daily_inr" | "monthly_inr", value: string, current: number) => {
    const n = Math.round(Number(value));
    if (!Number.isFinite(n) || n < 0 || n === Math.round(current)) return;
    onLimits({ [field]: n });
  };
  return (
    <div className="space-y-space-2 border-t border-subtle pt-space-3" data-limits>
      <h3 className="text-t13 font-semibold text-fg">Limits</h3>
      <p className="text-t14 text-fg" data-limits-line>{limits.line}</p>
      <div className="h-2 w-full overflow-hidden rounded-pill bg-surface-2" role="progressbar" aria-label="AI this month"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className={`h-full rounded-pill ${limits.reached ? "bg-warn" : "bg-brand"}`} style={{ width: `${pct}%` }} />
      </div>
      {limits.reached && (
        <p className="text-t13 text-warn" data-limit-reached>
          {limits.reached === "day" ? "Today's limit is reached. AI starts again tomorrow." : "This month's limit is reached. AI starts again on the 1st."}
        </p>
      )}
      <details className="rounded-control border border-subtle">
        <summary className="flex min-h-11 cursor-pointer items-center px-space-3 text-t14 font-semibold text-fg md:min-h-9">Change limits</summary>
        <div className="grid gap-space-3 border-t border-subtle p-space-3 sm:grid-cols-2">
          <label htmlFor={dayId} className="space-y-space-1 text-t13 text-fg">
            <span className="block">Each day (₹)</span>
            <input id={dayId} type="number" inputMode="numeric" min={0} max={Math.round(limits.max_cap_inr)} defaultValue={Math.round(limits.daily_cap_inr)}
              onBlur={(e) => commit("daily_inr", e.target.value, limits.daily_cap_inr)}
              className="h-11 w-full rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
          </label>
          <label htmlFor={monthId} className="space-y-space-1 text-t13 text-fg">
            <span className="block">Each month (₹)</span>
            <input id={monthId} type="number" inputMode="numeric" min={0} max={Math.round(limits.max_cap_inr)} defaultValue={Math.round(limits.monthly_cap_inr)}
              onBlur={(e) => commit("monthly_inr", e.target.value, limits.monthly_cap_inr)}
              className="h-11 w-full rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
          </label>
        </div>
      </details>
    </div>
  );
}

/** The key guides in a drawer, free Google Gemini first (A7). Pasting happens in the panel itself. */
export function KeyGuidesDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <RD.Root open={open} onOpenChange={onOpenChange}>
      <RD.Portal>
        <RD.Overlay className="fixed inset-0 z-40 bg-scrim/50" />
        <RD.Content aria-describedby={undefined} data-key-guides-drawer
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col overflow-y-auto border-l border-subtle bg-surface pb-[env(safe-area-inset-bottom)] text-fg shadow-raised focus:outline-none">
          <div className="flex items-center justify-between px-space-4 py-space-2">
            <RD.Title className="text-t15 font-semibold text-fg">Get a free key</RD.Title>
            <RD.Close aria-label="Close" className="-mr-space-2 flex h-11 w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg">
              <X size={18} aria-hidden />
            </RD.Close>
          </div>
          <div className="px-space-4 pb-space-6"><KeyGuidesV2 pasteHelper={false} startOpen="gemini" /></div>
        </RD.Content>
      </RD.Portal>
    </RD.Root>
  );
}

function refusalText(e: unknown): string {
  return e instanceof ApiError && e.detail ? e.detail : friendlyError(e);
}

export interface ConnectAiPanelV2Props {
  /** Called with the summary after every read and change (the Copilot shows its chip from it). */
  onChange?: (conn: AiConnection | null) => void;
}

/** The panel with its server state. Renders nothing while `ai_one_key` is off (the server answers 404). */
export default function ConnectAiPanelV2({ onChange }: ConnectAiPanelV2Props) {
  const [conn, setConn] = useState<AiConnection | null>(null);
  const [off, setOff] = useState(false);
  const [raw, setRaw] = useState("");
  const [picked, setPicked] = useState<AiProviderName | null>(null);
  const [consent, setConsent] = useState(false);
  const [step, setStep] = useState<Step>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [fixing, setFixing] = useState(false);
  const [guides, setGuides] = useState(false);
  const change = useRef(onChange);
  change.current = onChange;

  const apply = useCallback((next: AiConnection | null, announce = false) => {
    setConn(next);
    change.current?.(next);
    if (announce) window.dispatchEvent(new Event(AI_CONNECTION_CHANGED_EVENT));
  }, []);

  useEffect(() => {
    let live = true;
    api.aiConnection().then((c) => { if (live) apply(c); }).catch(() => { if (live) { setOff(true); change.current?.(null); } });
    return () => { live = false; };
  }, [apply]);

  const run = useCallback(async (work: () => Promise<AiConnection>, after?: () => void) => {
    setStep("busy");
    setNotice(null);
    try {
      apply(await work(), true);
      after?.();
    } catch (e) {
      setNotice(refusalText(e));
    } finally {
      setStep("idle");
    }
  }, [apply]);

  // No Save button: once the key's service is known and the consent line is ticked, it saves and tests by itself.
  useEffect(() => {
    if (!readyToConnect(raw, picked, consent, step)) return;
    const key = cleanKey(raw);
    const provider = chosenProvider(detectProvider(raw), picked) as AiProviderName;
    setRaw("");                                            // never kept in the form once sent
    void run(() => api.aiConnect({ api_key: key, provider, consent: true }), () => { setFixing(false); setPicked(null); });
  }, [raw, picked, consent, step, run]);

  if (off || !conn) return null;
  return (
    <>
      <ConnectAiView conn={conn} raw={raw} picked={picked} consent={consent} step={step} notice={notice} fixing={fixing}
        onRaw={(v) => { setRaw(v); setNotice(null); }} onPick={setPicked} onConsent={setConsent}
        onDisconnect={() => void run(() => api.aiDisconnect(), () => { setConsent(false); setFixing(false); })}
        onFix={() => setFixing(true)} onGuides={() => setGuides(true)}
        onAcknowledge={(kind) => void run(() => api.aiAcknowledge(kind))}
        onCopy={() => void run(() => api.aiCopyTenantKey(true))}
        onLimits={(body) => void run(() => api.aiSaveLimits(body))} />
      <KeyGuidesDrawer open={guides} onOpenChange={setGuides} />
    </>
  );
}

/** The Copilot's small chip once connected: "AI connected · Claude", linking to the panel under Settings. */
export function ConnectAiChip({ label }: { label: string }) {
  return (
    <Link to={SETTINGS_PATH.ai} data-ai-chip
      className="inline-flex min-h-11 items-center gap-space-1 rounded-pill border border-border bg-surface-1 px-space-3 text-t13 font-medium text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-8">
      <CheckCircle2 size={14} aria-hidden className="shrink-0 text-up" /> AI connected · {label}
    </Link>
  );
}

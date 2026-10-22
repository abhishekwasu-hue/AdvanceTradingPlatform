import { CheckCircle2, ExternalLink, ImageIcon, KeyRound, Sparkles } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { LINK_PRIMARY, LINK_SECONDARY } from "../dashboard/CardV2";
import { formatDateIST } from "../ui/format";
import type { ByokProviderName } from "../types";
import { BYOK_CHANGED_EVENT, PROVIDER_NAMES, checkPastedKey } from "./detect";
import { KEY_GUIDES, fixFor, guideFor, type KeyGuide } from "./guides";

/**
 * MO2 part B: Settings → AI, "Get an AI key" - the beginner guides (src/aiKeys/guides.ts) and the paste helper.
 * - The explainer line, then "Start with free Google Gemini" (one click opens that guide), then each provider with its
 *   tag (Free tier / Paid) and its guide: the key page button, numbered steps (a marked placeholder where a screenshot
 *   will go), what the key looks like, "Is it free?", the common problems and the day the guide was last checked.
 * - The paste helper trims what was pasted, says which provider the key belongs to and warns on another provider's
 *   key (one click switches the guide). "Save and test" uses the BYOK path that already exists: the key is saved
 *   encrypted (`PUT /api/ai/byok/key`) and tested with one tiny call (`POST /api/ai/byok/test/{provider}`); the key is
 *   cleared from the box once sent and never shown again. With BYOK off for the organisation the guides still show and
 *   the helper says the key cannot be saved yet.
 */

export const EXPLAINER = "An API key is like a password that lets AiAlgoDesk use an AI service on your account. Free keys take about 2 minutes to create.";


type Outcome = { ok: true; text: string } | { ok: false; text: string; fix: string | null };

const TAG = "inline-flex items-center rounded-pill px-space-2 py-px text-t12 font-medium";

function errorText(e: unknown): string {
  return e instanceof ApiError && e.detail ? e.detail : "The key could not be saved. Check your connection and try again.";
}

export default function KeyGuidesV2() {
  const [open, setOpen] = useState<ByokProviderName | null>(null);
  const [byokOn, setByokOn] = useState<boolean | null>(null);
  const guideRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    api.byokStatus().then(() => { if (live) setByokOn(true); }).catch(() => { if (live) setByokOn(false); });
    return () => { live = false; };
  }, []);

  const show = (p: ByokProviderName) => {
    setOpen(p);
    window.setTimeout(() => guideRef.current?.scrollIntoView({ block: "nearest" }), 0);
  };

  return (
    <section aria-labelledby="key-guides-title" data-key-guides className="rounded-panel border border-subtle bg-surface-1 p-space-4 text-fg shadow-card md:p-space-5">
      <div className="flex items-start gap-space-3">
        <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-brand/10 text-brand"><KeyRound size={20} /></span>
        <div className="min-w-0">
          <h2 id="key-guides-title" className="text-t15 font-semibold text-fg">Get an AI key</h2>
          <p className="mt-space-1 text-t14 text-fg-muted" data-explainer>{EXPLAINER}</p>
        </div>
      </div>

      <button type="button" onClick={() => show("gemini")} data-action="start-gemini" className={`${LINK_PRIMARY} mt-space-4 w-full sm:w-auto`}>
        <Sparkles size={16} aria-hidden /> Start with free Google Gemini
      </button>

      <ul className="mt-space-4 divide-y divide-subtle rounded-control border border-subtle" aria-label="AI providers">
        {KEY_GUIDES.map((g) => (
          <li key={g.provider} className="flex flex-wrap items-center gap-x-space-3 gap-y-space-2 px-space-3 py-space-3" data-provider={g.provider}>
            <span className="text-t14 font-semibold text-fg">{g.name}</span>
            <span className={`${TAG} ${g.cost === "Free tier" ? "bg-brand/10 text-brand" : "bg-surface-2 text-fg-muted"}`}>{g.cost}</span>
            {g.recommended && <span className={`${TAG} border border-brand text-brand`}>Recommended</span>}
            <button type="button" onClick={() => (open === g.provider ? setOpen(null) : show(g.provider))} aria-expanded={open === g.provider}
              aria-controls={`key-guide-${g.provider}`}
              className="ml-auto inline-flex min-h-11 items-center rounded-control px-space-2 text-t14 font-semibold text-brand hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
              {open === g.provider ? "Hide the guide" : "Show the guide"}
            </button>
          </li>
        ))}
      </ul>

      <div ref={guideRef}>
        {open && <Guide key={open} guide={guideFor(open)} byokOn={byokOn} onSwitch={show} />}
      </div>
    </section>
  );
}

function Guide({ guide, byokOn, onSwitch }: { guide: KeyGuide; byokOn: boolean | null; onSwitch: (p: ByokProviderName) => void }) {
  return (
    <div id={`key-guide-${guide.provider}`} data-key-guide={guide.provider} className="mt-space-4 space-y-space-4 border-t border-subtle pt-space-4">
      <div className="flex flex-wrap items-center justify-between gap-space-3">
        <h3 className="text-t15 font-semibold text-fg">{guide.name}: get your key</h3>
        <a href={guide.key_page.url} target="_blank" rel="noreferrer noopener" data-key-page className={LINK_SECONDARY}>
          {guide.key_page.label} <ExternalLink size={14} aria-hidden />
        </a>
      </div>

      <ol className="space-y-space-3" aria-label="Steps">
        {guide.steps.map((s, i) => (
          <li key={s.text} className="flex gap-space-3">
            <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-pill bg-surface-2 text-t13 font-semibold text-fg">{i + 1}</span>
            <div className="min-w-0 flex-1 space-y-space-2">
              <p className="text-t14 text-fg">{s.text}</p>
              {s.screenshot && (
                <div data-screenshot-placeholder className="flex items-center gap-space-2 rounded-control border border-dashed border-border bg-surface-2 px-space-3 py-space-3 text-t13 text-fg-muted">
                  <ImageIcon size={16} aria-hidden className="shrink-0" />
                  <span><span className="font-medium">Screenshot coming:</span> {s.screenshot}</span>
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>

      <dl className="grid gap-space-3 md:grid-cols-2">
        <div className="rounded-control border border-subtle p-space-3">
          <dt className="text-t13 font-semibold text-fg">What the key looks like</dt>
          <dd className="mt-space-1 text-t14 text-fg-muted">{guide.looks_like.text}</dd>
        </div>
        <div className="rounded-control border border-subtle p-space-3">
          <dt className="text-t13 font-semibold text-fg">Is it free?</dt>
          <dd className="mt-space-1 text-t14 text-fg-muted">{guide.is_it_free}</dd>
        </div>
      </dl>

      <PasteHelper guide={guide} byokOn={byokOn} onSwitch={onSwitch} />

      <details className="rounded-control border border-subtle">
        <summary className="flex min-h-11 cursor-pointer items-center px-space-3 text-t14 font-semibold text-fg">Common problems and fixes</summary>
        <ul className="divide-y divide-subtle border-t border-subtle">
          {guide.errors.map((e) => (
            <li key={e.problem} className="px-space-3 py-space-2">
              <p className="text-t14 font-medium text-fg">{e.problem}</p>
              <p className="text-t13 text-fg-muted">{e.fix}</p>
            </li>
          ))}
        </ul>
      </details>

      <p className="text-t12 text-fg-muted" data-checked-on={guide.checked_on}>
        Last checked against {guide.name}&apos;s own page on {formatDateIST(guide.checked_on)}. If a button has moved, the provider changed its page.
      </p>
    </div>
  );
}

function PasteHelper({ guide, byokOn, onSwitch }: { guide: KeyGuide; byokOn: boolean | null; onSwitch: (p: ByokProviderName) => void }) {
  const fieldId = useId();
  const noteId = useId();
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const check = checkPastedKey(guide.provider, raw);

  async function saveAndTest() {
    if (!check.ok || busy) return;
    const key = check.key;
    setRaw("");                                           // never kept in the form once sent
    setBusy(true);
    setOutcome(null);
    try {
      await api.byokSaveKey(guide.provider, key);
      const r = await api.byokTestKey(guide.provider);
      window.dispatchEvent(new Event(BYOK_CHANGED_EVENT));
      if (r.result.ok) setOutcome({ ok: true, text: `Your ${guide.name} key works. It is stored encrypted; only its first and last four characters are ever shown.` });
      else {
        const message = r.result.error ?? "The provider did not accept the key.";
        setOutcome({ ok: false, text: `The key was saved, but the test failed: ${message}`, fix: fixFor(guide, message)?.fix ?? null });
      }
    } catch (e) {
      const message = errorText(e);
      setOutcome({ ok: false, text: message, fix: fixFor(guide, message)?.fix ?? null });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-space-2 rounded-control bg-surface-2 p-space-3" data-paste-helper>
      <label htmlFor={fieldId} className="block text-t14 font-semibold text-fg">Paste your {guide.name} key</label>
      <div className="flex flex-col gap-space-2 sm:flex-row">
        <input id={fieldId} type="password" autoComplete="off" spellCheck={false} autoCapitalize="off" value={raw}
          onChange={(e) => { setRaw(e.target.value); setOutcome(null); }} aria-describedby={noteId}
          placeholder={`Starts with ${guide.looks_like.prefix}`}
          className="h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-1 px-space-3 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9" />
        <button type="button" onClick={() => void saveAndTest()} disabled={!check.ok || busy || byokOn !== true} data-action="save-test-key"
          className={`${LINK_PRIMARY} disabled:opacity-50`}>
          {busy ? "Testing…" : "Save and test"}
        </button>
      </div>
      <div id={noteId} aria-live="polite" className="space-y-space-1">
        {check.warning && (
          <p className="text-t13 text-warn" data-key-warning>
            {check.warning}{" "}
            {check.switchTo && (
              <button type="button" onClick={() => onSwitch(check.switchTo as ByokProviderName)}
                className="inline-flex min-h-11 items-center font-semibold text-brand hover:underline md:min-h-0">
                Use the {PROVIDER_NAMES[check.switchTo]} guide
              </button>
            )}
          </p>
        )}
        {check.ok && !outcome && <p className="text-t13 text-fg-muted" data-key-detected>This looks like a {guide.name} key.</p>}
        {byokOn === false && (
          <p className="text-t13 text-fg-muted" data-byok-off>Saving your own AI key is not turned on for your organisation yet. You can still create the key now.</p>
        )}
        {outcome?.ok && <p role="status" className="inline-flex items-center gap-space-1 text-t13 font-semibold text-up"><CheckCircle2 size={14} aria-hidden /> {outcome.text}</p>}
        {outcome && !outcome.ok && (
          <p role="alert" className="text-t13 text-fg">{outcome.text}{outcome.fix && <span className="block text-fg-muted">What to do: {outcome.fix}</span>}</p>
        )}
      </div>
    </div>
  );
}

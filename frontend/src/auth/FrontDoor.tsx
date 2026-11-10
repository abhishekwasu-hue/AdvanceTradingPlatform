import { Check, ImageIcon, PlayCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { brokerDisplayName } from "../components/staticIps";
import { FRONT_BUNDLES, FRONT_LANGS, type FrontLang, type FrontStrings } from "../i18n/frontDoor";

/**
 * M95 F2 / F4 / F5 (owner order M95, docs/design/M95_FRONT_DOOR.md): the front door - what a signed-out visitor sees
 * around the sign-in card at any address (SignInScreen, "Sign in" mode; same URL, no extra click):
 * - one line saying what the app is;
 * - six benefit cards, each with an image slot (`/front/<slug>.png`, made by another PR; a neutral placeholder while
 *   the file is missing);
 * - "Start in 3 steps", the three trust lines, the FAQ inside <details>, the M94 footer sentence;
 * - F4: a "Watch the 60-second intro" link only while the operator has set one (Admin › Growth › Learn videos);
 * - F5: English / Marathi / Hindi for these strings only (src/i18n/frontDoor.ts), the choice kept in this browser.
 * Light on purpose (fast first paint on a phone): no chart, no i18n library, images lazy with fixed sizes.
 * Text rules: no profit / accuracy claims, no testimonials, no trade-call words; every answer says only what the code does.
 */

export const FRONT_LANG_KEY = "atp_front_door_lang";

/** The image slots, in order. The image files are `/front/<slug>.png` at IMAGE_W x IMAGE_H. */
export const BENEFITS = [
  { slug: "copilot", title: "copilotTitle", text: "copilotText" },
  { slug: "option-chain", title: "chainTitle", text: "chainText" },
  { slug: "find-setups", title: "setupsTitle", text: "setupsText" },
  { slug: "paper-bot", title: "botTitle", text: "botText" },
  { slug: "journal", title: "journalTitle", text: "journalText" },
  { slug: "daily-report", title: "reportTitle", text: "reportText" },
] as const satisfies readonly { slug: string; title: keyof FrontStrings; text: keyof FrontStrings }[];
export const IMAGE_W = 640;
export const IMAGE_H = 400;

/** The stored language, "en" when none (or storage is blocked). */
export function readFrontLang(): FrontLang {
  try {
    const v = window.localStorage.getItem(FRONT_LANG_KEY);
    return v === "mr" || v === "hi" ? v : "en";
  } catch {
    return "en";   // intentional: storage blocked (private window) - English
  }
}

/** Keeps the choice in this browser (nothing when storage is blocked). */
export function storeFrontLang(lang: FrontLang): void {
  try { window.localStorage.setItem(FRONT_LANG_KEY, lang); } catch { /* intentional: not kept when storage is blocked */ }
}

export function useFrontLang(): [FrontLang, (lang: FrontLang) => void] {
  const [lang, setLang] = useState<FrontLang>(() => (typeof window === "undefined" ? "en" : readFrontLang()));
  const choose = (next: FrontLang) => {
    setLang(next);
    storeFrontLang(next);
  };
  return [lang, choose];
}

/** A link the front door may show: a plain https address only (the server checks it too). */
export function safeHttps(url: unknown): string | null {
  if (typeof url !== "string" || !url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && !u.username && !u.password ? u.toString() : null;
  } catch {
    return null;
  }
}

/** The FAQ's broker answer: the broker list the server reports, or the plain sentence. */
export function brokersAnswer(t: FrontStrings, brokers: readonly string[] | null): string {
  const names = (brokers ?? []).filter((b) => /^[a-z0-9_]+$/.test(b)).map(brokerDisplayName);
  return names.length ? t.faqBrokersA.replace("{brokers}", names.join(", ")) : t.faqBrokersNone;
}

const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export function LanguageSwitch({ lang, onChange }: { lang: FrontLang; onChange: (lang: FrontLang) => void }) {
  return (
    <div role="group" aria-label={FRONT_BUNDLES[lang].langLabel} data-front-lang
      className="flex items-center rounded-pill border border-subtle bg-surface-1 p-0.5">
      {FRONT_LANGS.map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={l === lang} onClick={() => onChange(l)}
          className={`min-h-9 rounded-pill px-space-3 text-t12 font-medium ${FOCUS} ${l === lang ? "bg-brand text-on-brand" : "text-fg-muted hover:text-fg"}`}>
          {FRONT_BUNDLES[l].langName}
        </button>
      ))}
    </div>
  );
}

export function FrontHeadline({ t }: { t: FrontStrings }) {
  return <p className="max-w-[36rem] text-center text-t18 font-semibold text-fg lg:text-left lg:text-t24" data-front-headline>{t.headline}</p>;
}

function BenefitImage({ slug, alt }: { slug: string; alt: string }) {
  const [missing, setMissing] = useState(false);
  if (missing) {
    return (
      <div aria-hidden="true" data-front-image-placeholder={slug}
        className="flex aspect-[16/10] w-full items-center justify-center rounded-control bg-surface-2 text-fg-muted">
        <ImageIcon size={28} />
      </div>
    );
  }
  return (
    <img src={`/front/${slug}.png`} alt={alt} width={IMAGE_W} height={IMAGE_H} loading="lazy" decoding="async"
      onError={() => setMissing(true)} data-front-image={slug}
      className="aspect-[16/10] h-auto w-full rounded-control bg-surface-2 object-cover" />
  );
}

/** F4: the intro video link (opens the video's own page in a new tab; the page's security policy plays no outside media). */
function IntroVideo({ t, url }: { t: FrontStrings; url: string | null }) {
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" data-front-video
      className={`flex min-h-12 items-center justify-center gap-space-2 rounded-panel border border-subtle bg-surface-1 px-space-4 text-t14 font-medium text-brand hover:bg-surface-2 ${FOCUS}`}>
      <PlayCircle size={20} aria-hidden />
      <span>{t.videoLabel}</span>
    </a>
  );
}

/** Everything under the sign-in card: the video link, the benefits, the steps, the trust lines and the FAQ. */
export function FrontDoorDetails({ t, googleOn, brokers, videoUrl }: {
  t: FrontStrings; googleOn: boolean; brokers: readonly string[] | null; videoUrl: string | null;
}) {
  const steps = [googleOn ? t.step1Google : t.step1Email, t.step2, t.step3];
  return (
    <div className="space-y-space-6" data-front-door-details>
      <IntroVideo t={t} url={videoUrl} />
      <section aria-labelledby="front-benefits">
        <h2 id="front-benefits" className="text-t15 font-semibold text-fg">{t.benefitsTitle}</h2>
        <ul className="mt-space-3 grid grid-cols-1 gap-space-3 sm:grid-cols-2">
          {BENEFITS.map((b) => (
            <li key={b.slug} data-front-benefit={b.slug} className="rounded-panel border border-subtle bg-surface-1 p-space-3 shadow-card">
              <BenefitImage slug={b.slug} alt={t.screenOf.replace("{title}", t[b.title])} />
              <p className="mt-space-3 text-t15 font-semibold text-fg">{t[b.title]}</p>
              <p className="mt-space-1 text-t14 text-fg-muted">{t[b.text]}</p>
            </li>
          ))}
        </ul>
      </section>
      <section aria-labelledby="front-steps">
        <h2 id="front-steps" className="text-t15 font-semibold text-fg">{t.stepsTitle}</h2>
        <ol className="mt-space-3 space-y-space-2" data-front-steps>
          {steps.map((s, i) => (
            <li key={i} className="flex items-center gap-space-3 rounded-panel border border-subtle bg-surface-1 px-space-3 py-space-2 text-t14 text-fg">
              <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand text-t14 font-semibold text-on-brand">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </ol>
      </section>
      <ul className="space-y-space-2" data-front-trust>
        {[t.trust1, t.trust2, t.trust3].map((line) => (
          <li key={line} className="flex items-center gap-space-2 text-t14 text-fg">
            <Check size={18} aria-hidden className="shrink-0 text-up" />
            <span>{line}</span>
          </li>
        ))}
      </ul>
      <section aria-labelledby="front-faq">
        <h2 id="front-faq" className="text-t15 font-semibold text-fg">{t.faqTitle}</h2>
        <div className="mt-space-3 divide-y divide-subtle overflow-hidden rounded-panel border border-subtle bg-surface-1" data-front-faq>
          {[[t.faqCostQ, t.faqCostA], [t.faqBrokersQ, brokersAnswer(t, brokers)], [t.faqSafeQ, t.faqSafeA], [t.faqPaperQ, t.faqPaperA]].map(([q, a]) => (
            <details key={q} className="group">
              <summary className={`flex min-h-12 cursor-pointer list-none items-center justify-between gap-space-3 px-space-4 text-t14 font-medium text-fg ${FOCUS}`}>
                <span>{q}</span>
                <span aria-hidden="true" className="text-fg-muted transition-transform group-open:rotate-180">▾</span>
              </summary>
              <p className="px-space-4 pb-space-3 text-t14 text-fg-muted">{a}</p>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}

/** The broker list and the intro video, read once without a login (each left empty when the read fails). */
export function useFrontDoorData(on: boolean): { brokers: string[] | null; videoUrl: string | null } {
  const [brokers, setBrokers] = useState<string[] | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let live = true;
    request<{ brokers: string[] }>("/broker/available")
      .then((r) => { if (live) setBrokers(Array.isArray(r.brokers) ? r.brokers : null); })
      .catch(() => undefined);   // intentional: the plain sentence stays
    request<{ url: string | null }>("/site/intro-video")
      .then((r) => { if (live) setVideoUrl(safeHttps(r.url)); })
      .catch(() => undefined);   // intentional: no video link
    return () => { live = false; };
  }, [on]);
  return { brokers, videoUrl };
}

export { FRONT_BUNDLES };
export type { FrontLang, FrontStrings };

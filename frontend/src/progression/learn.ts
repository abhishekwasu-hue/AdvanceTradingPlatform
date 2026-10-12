// N2-10: the method library's structure - which concept links to which screens (screener taxonomy setup keys), which
// P1-d checklist item records it and which Z6 journal tags show it skipped - and the only external links allowed:
// official NSE, SEBI and income-tax (CBDT) sites. The words live in locales/learn.en.ts.

export const CONCEPTS = ["trend", "level", "pullback", "commitment", "rr"] as const;
export type Concept = (typeof CONCEPTS)[number];

export const CONCEPT_LINKS: Record<Concept, { setups: string[]; checklist: string; tags: string[] }> = {
  trend: { setups: ["momentum"], checklist: "trend_aligned", tags: ["revenge_trade", "overtrading"] },
  level: { setups: ["reversal", "mean_reversion"], checklist: "level_identified", tags: ["no_stop"] },
  pullback: { setups: ["pullback"], checklist: "pullback_confirmed", tags: ["fomo_entry", "averaging_loser"] },
  commitment: { setups: ["breakout", "reversal"], checklist: "commitment_candle", tags: ["fomo_entry"] },
  rr: { setups: [], checklist: "rr_ok", tags: ["moved_stop_against", "early_exit", "held_through_event"] },
};

export const GLOSSARY = ["r_multiple", "expectancy", "profit_factor", "drawdown", "stop_loss", "time_stop", "invalidation", "risk_cap",
  "daily_loss_limit", "support_resistance", "demand_supply", "swing", "breakout", "atr", "iv", "pop", "expected_move", "theta", "delta",
  "lot_size", "charges"] as const;

/** The only hosts an education page may link to: official NSE, SEBI and CBDT sites. */
export const OFFICIAL_HOSTS = ["www.sebi.gov.in", "sebi.gov.in", "investor.sebi.gov.in", "www.nseindia.com", "nseindia.com",
  "www.incometaxindia.gov.in", "incometaxindia.gov.in", "www.incometax.gov.in", "incometax.gov.in"];

/** SEBI's official Reports and Statistics - Research list, where the F&O profit and loss study (25 Jan 2023) and its
 * later updates are listed by name. */
export const SEBI_STUDY_LINK = "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=4&ssid=81&smid=109";

export const OFFICIAL_LINKS: { key: "sebiStudy" | "sebi" | "nse" | "incomeTax"; href: string }[] = [
  { key: "sebiStudy", href: SEBI_STUDY_LINK },
  { key: "sebi", href: "https://investor.sebi.gov.in/" },
  { key: "nse", href: "https://www.nseindia.com/" },
  { key: "incomeTax", href: "https://www.incometaxindia.gov.in/" },
];

export function isOfficial(href: string): boolean {
  try {
    const u = new URL(href);
    return u.protocol === "https:" && OFFICIAL_HOSTS.includes(u.hostname);
  } catch { return false; }
}

/** The concept a URL hash names ("#learn-pullback"), else the first one. */
export function conceptFromHash(hash: string): Concept {
  const key = hash.replace(/^#learn-/, "");
  return (CONCEPTS as readonly string[]).includes(key) ? (key as Concept) : "trend";
}

import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * M75 rule 1 guard (owner order M75, design docs/design/M75_NO_NAVIGATION.md): a click never navigates the user away
 * from the tab he is working in; every facility opens in place (side panel, drawer, bottom sheet, modal, split view).
 *
 * It counts the click-navigation sites left in app source (tests, stories and fixtures excluded):
 * - `navigate(...)` calls (a call with `replace: true` is address clean-up, not a jump, and is not counted);
 * - `<Link ...>` elements;
 * - `<a href=...>` to an app route (in-page `#` anchors, `mailto:` and links that open an external site in a new
 *   browser tab are not counted; an app route opened in a new browser tab IS counted);
 * - `location.assign(...)`, `location.replace(...)`, `location.href = ...`, `window.open(...)`, `history.pushState(...)`;
 * - an inline destination handed to a renderer: `more={{ ... to: ... }}`, `action={{ ... to: ... }}` and a toolbar /
 *   menu item `{ id: "...", ..., to: ... }`.
 * The exclusions (the main navigation, auth, external links, the public website, address redirects) are listed below,
 * each with its reason; they match the exclusions in the design doc.
 *
 * BASELINE is a ratchet: the count may only go DOWN. When a PR converts sites to the in-place host, lower BASELINE to
 * the new count printed by this test (the test also fails when the count is lower, so the ratchet never goes slack).
 * `M75_NAV_DUMP=<file>` writes the current list as JSON (used to build the inventory table in the design doc).
 */
const BASELINE = 122;

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = join(ROOT, "src");

/** Whole files that are not counted, with the reason. */
const EXCLUDED_FILES: Record<string, string> = {
  "src/shell/SidebarV2.tsx": "NAV - the sidebar is how the user picks a tab",
  "src/shell/BottomNavV2.tsx": "NAV - the phone bottom bar",
  "src/shell/MoreSheetV2.tsx": "NAV - the phone More sheet (the rest of the main navigation)",
  "src/shell/PageTabsV2.tsx": "NAV - a page's own sub-tab bar",
  "src/ui/SubTabs.tsx": "NAV - the shared sub-tab bar",
  "src/components/Sidebar.tsx": "NAV - the classic sidebar",
  "src/components/BottomNav.tsx": "NAV - the classic bottom bar",
  "src/site/SitePage.tsx": "PUBLIC - the signed-out public website, not an app tab",
  "src/site/SiteLinks.tsx": "PUBLIC - the public website's own links",
  "src/legal/LegalPage.tsx": "PUBLIC - the public legal pages",
  "src/legal/markdown.tsx": "PUBLIC - links between the legal documents",
  "src/legal/LegalConsentField.tsx": "AUTH - the sign-up consent field (opens the document in a new tab before an account exists)",
  "src/status/StatusView.tsx": "PUBLIC - the public status page",
  "src/auth/SignInScreen.tsx": "AUTH - sign-in",
  "src/auth/google.ts": "AUTH - Google sign-in return",
  "src/auth/GoogleSignInButton.tsx": "AUTH - Google sign-in",
  "src/native/deepLinks.ts": "DEEP - a deep link opened from outside the app (notification tap, app link)",
  "src/ui/brokerSession.ts": "EXTERNAL - the broker's own login page (OAuth)",
  "src/settings/model.ts": "NAV - the Settings tab's own menu (one row per section)",
  "src/shell/common.tsx": "NAV - the 'page not found' screen's way home (no tab is being worked in)",
  "src/ui/ComingSoon.tsx": "NAV - a 'coming soon' placeholder's way home (no tab is being worked in)",
  "src/inplace/InPlaceProvider.tsx": "INPLACE - the in-place host itself (NAV-1): it writes `?panel=` on the SAME path and Back closes; it navigates (as today, or in a new browser tab where the button did so before) only for an address no panel is registered for, with a dev warning",
  "src/inplace/PanelRouter.tsx": "INPLACE - a panel's own address (NAV-2): it moves only inside the panel; a page in the panel that goes to another address is handed to the host",
  "src/inplace/InPlaceLink.tsx": "INPLACE - the in-place link (NAV-1): the real address for a long-press / new tab; a plain click opens the panel registered for it (an area PR registers the panel when it switches a site to it)",
};

/**
 * Single sites that are not counted: a file and a text the site's own span contains (or, with `line`, a text of the
 * source line the site starts on, for two sites whose spans are the same), with the reason.
 */
const EXCLUDED_SITES: { file: string; text?: RegExp; line?: RegExp; reason: string }[] = [
  { file: "*", text: /SIGN_IN_(?:PAGE_)?PATH/, reason: "AUTH - sign in" },
  { file: "src/App.tsx", text: /pathFor\("account"\)/, reason: "NAV - the account entry of the classic top bar" },
  { file: "src/App.tsx", text: /^navigate\(pathFor\(p\)\)/, reason: "NAV - the classic sidebar's handler" },
  { file: "src/shell/AppShellV2.tsx", text: /^navigate\(pathFor\(p\)\)/, reason: "NAV - the sidebar's handler" },
  { file: "src/shell/AppShellV2.tsx", text: /^navigate\(path\)/, reason: "NAV - the command bar (the keyboard way to pick a tab)" },
  { file: "src/shell/TopBarV2.tsx", text: /aria-label="AiAlgoDesk/, reason: "NAV - the logo (home)" },
  { file: "src/shell/TopBarV2.tsx", text: /title=\{user \? "Account"/, reason: "NAV - the account entry" },
  { file: "src/pages/AiCopilotPage.tsx", text: /navigate\(copilotPath\(next\)\)/, reason: "NAV - the Copilot sub-tab bar" },
  { file: "src/copilot/chat/CopilotTabsV2.tsx", text: /./, reason: "NAV - the Copilot sub-tab bar" },
  { file: "src/fno/SymbolHubTabsV2.tsx", text: /./, reason: "NAV - the symbol hub's sub-tab bar" },
  { file: "src/settings/SettingsIndexV2.tsx", text: /to=\{row\.to\}|to=\{h\.to\}|navigate\(first\.to\)/, reason: "NAV - the Settings tab's own menu and its search" },
  { file: "src/settings/SettingsSectionV2.tsx", text: /data-back/, reason: "NAV - back to the Settings menu" },
  { file: "src/chart/ChartsPageV2.tsx", text: /^navigate\(`\$\{chartsPath\((?:p\.symbol|sym)\)/, reason: "SAME-TAB - the Chart tab changes its own symbol (the symbol is part of its address)" },
  { file: "src/trading/RiskV2.tsx", text: /to="\/account"/, reason: "AUTH - sign in" },
  { file: "src/shell/askAiEntry.tsx", text: /^\{ id: "(?:analyze|copilot)",/, reason: "INPLACE - an Ask AI sheet row: the sheet opens it with <InPlaceLink> (NAV-2), in place" },
  { file: "src/admin/ViewAsBanner.tsx", text: /./, reason: "AUTH - admin 'view as' start / stop switches the signed-in identity" },
  { file: "src/broker/BrokerPromptHost.tsx", line: /if \(!onBrokerPage\) navigate\(BROKER_PATH\)/, reason: "AUTH - the first sign-in step of a new account (not a click)" },
  { file: "src/pages/ChartWindow.tsx", text: /location\.replace\(chartWindowRedirect/, reason: "REDIRECT - an old address forwarded on load (not a click)" },
  { file: "src/pages/ChartWorkspace.tsx", text: /location\.replace\(workspaceRedirect/, reason: "REDIRECT - an old address forwarded on load (not a click)" },
  { file: "src/components/BrokerTokenBanner.tsx", text: /window\.open\("about:blank"|location\.href = authorization_url/, reason: "EXTERNAL - the broker's login page" },
  { file: "src/broker/BrokerAdvanced.tsx", text: /location\.href = authorization_url/, reason: "EXTERNAL - the broker's login page" },
  { file: "src/broker/ConnectPanel.tsx", text: /window\.open\("about:blank"|location\.href = authorization_url/, reason: "EXTERNAL - the broker's login page" },
  { file: "src/alerts/TelegramConnectV2.tsx", text: /window\.open\(|location\.href = made\.url/, reason: "EXTERNAL - Telegram" },
  { file: "src/admin/AlertsPanelV2.tsx", text: /window\.open\(url/, reason: "EXTERNAL - Telegram" },
];

function files(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) files(full, out);
    else if (/\.(tsx?|jsx?)$/.test(name) && !/\.(test|spec|stories)\.(tsx?|jsx?)$/.test(name) && !/fixture/i.test(name)) out.push(full);
  }
  return out;
}

export interface NavSite { file: string; line: number; kind: string; text: string; source: string }

/** The text of a call from its opening parenthesis to the matching close (at most 400 characters). */
function callSpan(source: string, open: number): string {
  let depth = 0;
  for (let i = open; i < source.length && i < open + 400; i += 1) {
    if (source[i] === "(") depth += 1;
    else if (source[i] === ")") { depth -= 1; if (depth === 0) return source.slice(open, i + 1); }
  }
  return source.slice(open, open + 400);
}

/** The text of a JSX opening tag from `<` to its closing `>` (an arrow `=>` inside an attribute is skipped). */
function tagSpan(source: string, start: number): string {
  let braces = 0;
  for (let i = start + 1; i < source.length && i < start + 1200; i += 1) {
    const c = source[i];
    if (c === "{") braces += 1;
    else if (c === "}") braces -= 1;
    else if (c === ">" && braces === 0 && source[i - 1] !== "=") return source.slice(start, i + 1);
  }
  return source.slice(start, start + 400);
}

const NEW_TAB = /target(?:=|:\s*)["']_blank["']|target=\{["']_blank["']\}/;
/** An `href` value that is an app route even when it opens in a new browser tab. */
const INTERNAL_IN_NEW_TAB = /href=\{(?:openUrl|links\.(?:ce|pe)|legalPath\()|href="\//;
const NOT_A_ROUTE = /href=(?:"#|\{`#|"mailto:|\{`mailto:)/;

/** The click-navigation sites in one source text (before the exclusions). */
export function navigationSites(source: string, file = "x.tsx"): NavSite[] {
  const out: NavSite[] = [];
  const lineOf = (i: number) => source.slice(0, i).split("\n").length;
  const lines = source.split("\n");
  const add = (i: number, kind: string, text: string) => {
    const where = lineOf(i);
    if (/^\s*(?:\*|\/\/|\/\*)/.test(lines[where - 1] ?? "")) return;   // a comment, not code
    out.push({ file, line: where, kind, text: text.replace(/\s+/g, " ").trim().slice(0, 160), source: (lines[where - 1] ?? "").trim() });
  };

  for (const m of source.matchAll(/(?<![\w$.])navigate\(/g)) {
    const span = `navigate${callSpan(source, (m.index ?? 0) + "navigate".length)}`;
    if (/replace:\s*true/.test(span)) continue;            // address clean-up / canonical address, not a jump
    add(m.index ?? 0, "navigate", span);
  }
  for (const m of source.matchAll(/<Link\b/g)) add(m.index ?? 0, "Link", tagSpan(source, m.index ?? 0));
  for (const m of source.matchAll(/<a\s/g)) {
    const tag = tagSpan(source, m.index ?? 0);
    if (!/\bhref=/.test(tag) || NOT_A_ROUTE.test(tag)) continue;
    if (NEW_TAB.test(tag) && !INTERNAL_IN_NEW_TAB.test(tag)) continue;   // an external site in a new browser tab
    add(m.index ?? 0, "a-href", tag);
  }
  const calls = /\blocation\.(?:assign|replace)\(|\bwindow\.open\(|\bhistory\.pushState\(/g;
  for (const m of source.matchAll(calls)) {
    const at = (m.index ?? 0) + m[0].length - 1;
    add(m.index ?? 0, m[0].replace(/\($/, ""), `${m[0].slice(0, -1)}${callSpan(source, at)}`);
  }
  for (const m of source.matchAll(/\blocation\.href\s*=(?!=)[^;\n]*/g)) add(m.index ?? 0, "location.href", m[0]);
  for (const m of source.matchAll(/\b(?:more|action)=\{\{[^}\n]*\bto:[^}\n]*\}\}/g)) add(m.index ?? 0, "to-prop", m[0]);
  for (const m of source.matchAll(/\{\s*id:\s*"[\w-]+",[^\n]*?\bto:\s*[^,}\s][^\n]*/g)) add(m.index ?? 0, "to-item", m[0]);
  return out.sort((a, b) => a.line - b.line);
}

function excluded(site: NavSite): boolean {
  if (EXCLUDED_FILES[site.file]) return true;
  return EXCLUDED_SITES.some((x) => (x.file === "*" || x.file === site.file)
    && (!x.text || x.text.test(site.text)) && (!x.line || x.line.test(site.source)));
}

function appSites(): NavSite[] {
  return files(SRC)
    .map((f) => relative(ROOT, f).split("\\").join("/"))
    .flatMap((f) => navigationSites(readFileSync(join(ROOT, f), "utf8"), f))
    .filter((s) => !excluded(s));
}

describe("no click-navigation guard (M75 rule 1)", () => {
  it("finds the shapes it is meant to find", () => {
    expect(navigationSites(`onClick={() => navigate("/signals")}`)).toHaveLength(1);
    expect(navigationSites(`navigate(".", { replace: true, state: null })`)).toHaveLength(0);
    expect(navigationSites(`navigate(copilotPath(tab),\n  { replace: true })`)).toHaveLength(0);
    expect(navigationSites(`<Link to="/positions" className="x">Open</Link>`)).toHaveLength(1);
    expect(navigationSites(`<a href="/settings/ai" className="x">Add your key</a>`)).toHaveLength(1);
    expect(navigationSites(`<a href={why.link} onClick={() => go()} className="x">Change</a>`)).toHaveLength(1);
    expect(navigationSites(`<a href={openUrl} target="_blank" rel="noopener">Open</a>`)).toHaveLength(1);
    expect(navigationSites(`<a href={sub.checkout_url} target="_blank" rel="noreferrer">Pay</a>`)).toHaveLength(0);
    expect(navigationSites(`<a href={\`#lesson-\${id}\`}>Lesson</a>`)).toHaveLength(0);
    expect(navigationSites(`<a href={\`mailto:\${email}\`}>Mail</a>`)).toHaveLength(0);
    expect(navigationSites(`window.location.assign("/option-chain/plan");`)).toHaveLength(1);
    expect(navigationSites(`window.open(chartWindowUrl(s), "_blank")`)).toHaveLength(1);
    expect(navigationSites(`a.href = url; a.download = name; a.click();`)).toHaveLength(0);
    expect(navigationSites(`<CardV2 more={{ label: "View all", to: "/positions" }} />`)).toHaveLength(1);
    expect(navigationSites(`[{ id: "open", label: "Open", to: chartTabPath(s) }]`)).toHaveLength(1);
    expect(navigationSites(`<button onClick={() => setOpen(true)}>Open</button>`)).toHaveLength(0);
  });

  it("the number of click-navigation sites in app source only goes down", () => {
    const found = appSites();
    const dump = process.env.M75_NAV_DUMP;
    if (dump) writeFileSync(dump, JSON.stringify(found.map((s) => ({ file: s.file, line: s.line, kind: s.kind, text: s.text })), null, 1));
    const listing = found.map((s) => `${s.file}:${s.line} [${s.kind}] ${s.text}`).join("\n");
    if (found.length !== BASELINE) console.log(`click-navigation sites (${found.length}):\n${listing}`);
    expect(found.length, `click-navigation sites grew past ${BASELINE} - open the facility in place (docs/design/M75_NO_NAVIGATION.md):\n${listing}`)
      .toBeLessThanOrEqual(BASELINE);
    expect(found.length, `fewer click-navigation sites than BASELINE (${BASELINE}) - lower BASELINE to ${found.length}`).toBe(BASELINE);
  });
});

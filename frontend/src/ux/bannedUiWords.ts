import ts from "typescript";

/**
 * UX3 fix 10: the banned UI words (UX audit §8) and the rules the guard (bannedUiWords.test.ts) reads page source with.
 * Test-only: no page imports this module, so it never reaches the bundle.
 */

export interface BannedWord { name: string; pattern: RegExp; kind: string }

/** The audit's §8 list. A word is matched whole; the case rule follows how the audit saw it on screen. */
export const BANNED_UI_WORDS: readonly BannedWord[] = [
  { name: "sample data", pattern: /\bsample (?:data|bars|candles|OHLCV|chain)\b/i, kind: "demo data" },
  { name: "Deterministic", pattern: /\bdeterministic\b/i, kind: "developer" },
  { name: "OHLCV", pattern: /\bOHLCV\b/, kind: "developer" },
  { name: "backend", pattern: /\bbackend\b/i, kind: "developer" },
  { name: "worker / heartbeat", pattern: /\b(?:background worker|heartbeat|beat \d+\s?s ago)\b/i, kind: "internal" },
  { name: "instrument master", pattern: /\binstrument master\b/i, kind: "internal" },
  { name: "tenant", pattern: /\btenants?\b/i, kind: "internal" },
  { name: "flag name", pattern: /\(feature [a-z0-9_]+\)|not (?:turned on|enabled) for (?:this|your) organisation/i, kind: "flag names" },
  { name: "registry", pattern: /\b(?:registry|auto-executable|persisted)\b/i, kind: "developer" },
  { name: "Deployment #", pattern: /\bdeployment\s*#/i, kind: "internal" },
  { name: "parser", pattern: /\b(?:parser|rule_based|nlu-parser)\b/i, kind: "developer" },
  { name: "HMAC / headers", pattern: /\b(?:HMAC|X-ATP-Signature|X-API-Key)\b/, kind: "developer" },
  { name: "SMTP", pattern: /\b(?:SMTP|STARTTLS|body template)\b/, kind: "developer" },
  { name: "BotFather", pattern: /\bBotFather\b/, kind: "developer" },
  { name: "bot token / chat id", pattern: /\b(?:bot token|chat id)\b/i, kind: "developer" },
  { name: "OAuth / redirect", pattern: /\b(?:OAuth|request token|redirect URL|redirect URI)\b/i, kind: "developer (U4)" },
  { name: "TOTP secret", pattern: /\bTOTP secret\b/i, kind: "developer (U4)" },
  { name: "developer console", pattern: /\bdeveloper console\b/i, kind: "developer (U4)" },
  { name: "lookback", pattern: /\blookback\b/i, kind: "developer (U4)" },
  { name: "enum code", pattern: /\b(?:TARGET_1|STOP_LOSS|RISK_REJECTION|NO_TRADE)\b/, kind: "enum codes" },
  { name: "indicator id", pattern: /\b(?:PLUS_DI|MINUS_DI|OR_HIGH|OR_LOW|VOLUME_SMA|BB_UPPER)\b/, kind: "internal ids" },
  { name: "Copilot internals", pattern: /\bshadow ×|\bSTALE \(\d/, kind: "internal" },
  { name: "hash chain", pattern: /\b(?:SHA-256|prev_hash|hash-chained)\b/, kind: "developer" },
  { name: "user agent", pattern: /\b(?:Python-urllib|HeadlessChrome)\b/, kind: "internal data" },
  { name: "scope id", pattern: /\b(?:ai:manage|trading:live)\b/, kind: "internal" },
  { name: "Entry · Stop · Target prices", pattern: /\bEntry\s+(?:₹|\d|\$\{).*\bStop\s+(?:₹|\d|\$\{)/, kind: "signal language (11l)" },
  { name: "Stop / Target price", pattern: /\b(?:Stop|Target)\s+(?:₹|\d|\$\{format)/, kind: "signal language (11l)" },
];

/**
 * The user pages the guard reads (paths under src/): the v2 pages and the parts only they use. Outside it: `admin/`
 * (the admin console may name internals), `pages/` and `components/` (the old pages, which only the flag-off shell
 * shows, and shared older parts - they leave with the page merges, UX3-9), `copilot/` (its own audit slice) and tests.
 */
export const USER_PAGE_ROOTS: readonly string[] = [
  "broker", "dashboard", "signals", "optionChain", "setup", "paperbot", "trading", "settings", "shell", "ui", "fno",
  "chartAnalyze", "tours", "aiKeys", "markets", "growth", "support",
  "chart/ChartsPageV2.tsx",
];

export interface Allowed { file: string; word: string; text?: string; reason: string }

/** Strings that may stay, each with its reason (file paths relative to src/). */
export const ALLOWED: readonly Allowed[] = [
  { file: "broker/BrokerAdvanced.tsx", word: "OAuth / redirect", reason: "Broker › Advanced (collapsed): the Upstox app sign-in for an API key and secret" },
  { file: "settings/model.ts", word: "bot token / chat id", reason: "search keywords for Settings › Alerts / Advanced, never shown as text" },
  { file: "signals/TryOnSymbolV2.tsx", word: "sample data", reason: "the try tool's Advanced fold, shown only to the platform admin with ui_v2 (UX3 fix 7)" },
  { file: "ui/glossary.ts", word: "OAuth / redirect", text: "Redirect URL", reason: "the glossary entry for the Redirect URL field, which sits under a broker's Advanced fold" },
];

/** JSX attributes whose string value a user reads. */
const TEXT_ATTRS = new Set(["placeholder", "title", "aria-label", "aria-description", "alt", "label"]);

/** A Tailwind class list ("rounded-control md:min-h-9 bg-brand"), not words for a person. */
export function isClassList(text: string): boolean {
  const tokens = text.trim().split(/\s+/);
  if (tokens.length === 0 || tokens[0] === "") return false;
  return tokens.every((t) => /^!?-?[a-z0-9[\]&:>*~@./_-]+$/.test(t) && /[-:[]/.test(t)) || tokens.every((t) => /^(?:[a-z0-9-]+:)+[a-z0-9[\]./_-]+$/.test(t));
}

function attributeName(node: ts.Node): string | null {
  for (let n: ts.Node | undefined = node.parent; n; n = n.parent) {
    if (ts.isJsxAttribute(n)) return n.name.getText();
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n) || ts.isJsxFragment(n)) return null;
  }
  return null;
}

function skipped(node: ts.Node): boolean {
  for (let n: ts.Node | undefined = node.parent; n; n = n.parent) {
    if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n) || ts.isLiteralTypeNode(n) || ts.isImportTypeNode(n)) return true;
    if (ts.isCallExpression(n)) {
      if (n.expression.kind === ts.SyntaxKind.ImportKeyword) return true;
      const callee = n.expression.getText();
      if (/^console\.|^(?:cx|cn|clsx)$/.test(callee)) return true;
    }
    if (ts.isPropertyAssignment(n) && n.name === node) return true;
    if (ts.isBlock(n) || ts.isSourceFile(n)) break;
  }
  return false;
}

/** Every string in a source file a user can see: JSX text, text attributes and sentence-like string literals. */
export function uiStrings(source: ts.SourceFile): { text: string; pos: number }[] {
  const out: { text: string; pos: number }[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) {
      const text = node.getText().replace(/\s+/g, " ").trim();
      if (text) out.push({ text, pos: node.getStart() });
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateExpression(node)) {
      const text = ts.isTemplateExpression(node) ? node.getText().slice(1, -1) : node.text;
      const attr = attributeName(node);
      const keep = attr !== null
        ? TEXT_ATTRS.has(attr)
        : !skipped(node) && /\s/.test(text.trim()) && !text.startsWith("/") && !isClassList(text);
      if (keep && text.trim()) out.push({ text, pos: node.getStart() });
      if (ts.isTemplateExpression(node)) ts.forEachChild(node, visit);
      return;
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return out;
}

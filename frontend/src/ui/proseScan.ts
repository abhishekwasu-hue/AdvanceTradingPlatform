import { readFileSync, existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import ts from "typescript";

/**
 * PROSE-1 (owner order 30 Oct, docs/reviews/PROSE_INVENTORY.md): the AST rule behind the "no theory text" guard. A
 * finding is rendered text of PROSE_MIN characters or more that a user sees every time the page opens:
 * - JSX text, or a string / template literal written as a JSX child or as a visible JSX attribute (a card's
 *   description, a field's hint - not title / aria-* / placeholder / className and the like);
 * - NOT inside <Details>, <details>, <HelpPopover> or <TermHint> (behind an ⓘ, closed by default);
 * - NOT conditional: no ?:, &&, ||, ??, if or switch between the text and its component, and not in a set* / toast /
 *   Error call (messages after an action).
 * Scope: the files a user reaches with ui_v2 ON - the import closure of the v2 pages (shell/pagesV2.ts), the pages the
 * v2 shell still takes from routes.ts, and the shell itself. Node only (the test reads the source tree).
 */
import { PROSE_MIN } from "./nodeText";

export { PROSE_MIN };

// PageFootnote draws its children inside its own "About" Details (src/ui/PageFootnote.tsx).
const HIDDEN_PARENTS = new Set(["Details", "details", "HelpPopover", "TermHint", "noscript", "PageFootnote"]);
const HIDDEN_ATTRS = /^(title|aria-.*|placeholder|alt|className|class|id|data-.*|href|to|key|role|type|name|value|htmlFor|src|lines|style|testId|dataAttrs)$/;
/** Props a shared component itself puts behind an ⓘ / Details (PROSE-1 root fixes). */
const HIDDEN_BY_COMPONENT: Record<string, ReadonlySet<string>> = {
  PageHeader: new Set(["line", "description"]),
  PageTitleV2: new Set(["subtitle"]),
  PageIntro: new Set(["lines"]),
  Panel: new Set(["note"]),
  Input: new Set(["hint"]),
  DataSourceBar: new Set(["note"]),
};
const MESSAGE_CALL = /^(set[A-Z]\w*|toast|error|warning|success|info|Error|TypeError|confirm|alert)$/;

export interface ProseFinding { file: string; line: number; text: string }

function normal(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function tagName(node: ts.JsxOpeningLikeElement): string {
  return node.tagName.getText();
}

/** The literal text of a string or template literal (substitutions dropped). */
function literalText(node: ts.Node): string | null {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) return node.head.text + node.templateSpans.map((s) => " " + s.literal.text).join("");
  return null;
}

/** True when the text is shown every time: no hidden parent, no condition up to its component. */
function alwaysShown(node: ts.Node): boolean {
  let n: ts.Node | undefined = node.parent;
  let child: ts.Node = node;
  while (n) {
    if (ts.isJsxElement(n) && HIDDEN_PARENTS.has(tagName(n.openingElement))) return false;
    // Screen-reader-only text (a table caption) is never on screen.
    if (ts.isJsxElement(n) && /\bsr-only\b/.test(n.openingElement.attributes.getText())) return false;
    // JSX given to a prop that its component itself puts behind the ⓘ (PageHeader `description={<>...</>}`).
    if (ts.isJsxAttribute(n) && !visibleAttr(n)) return false;
    if (ts.isConditionalExpression(n) && child !== n.condition) return false;
    if (ts.isBinaryExpression(n) && [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(n.operatorToken.kind)) return false;
    if (ts.isIfStatement(n) || ts.isCaseClause(n) || ts.isDefaultClause(n)) return false;
    if (ts.isCallExpression(n)) {
      const callee = n.expression;
      const name = ts.isIdentifier(callee) ? callee.text : ts.isPropertyAccessExpression(callee) ? callee.name.text : "";
      if (MESSAGE_CALL.test(name)) return false;
    }
    if (ts.isNewExpression(n) && ts.isIdentifier(n.expression) && MESSAGE_CALL.test(n.expression.text)) return false;
    if (ts.isJsxAttribute(n) && n.initializer && ts.isJsxExpression(n.initializer) && n.initializer.expression &&
        (ts.isArrowFunction(n.initializer.expression) || ts.isFunctionExpression(n.initializer.expression))) return false;
    if (ts.isFunctionDeclaration(n) || ts.isMethodDeclaration(n)) return true;
    if ((ts.isArrowFunction(n) || ts.isFunctionExpression(n)) && !(n.parent && ts.isCallExpression(n.parent))) return true;
    child = n;
    n = n.parent;
  }
  return true;
}

/** Only text that renders: inside JSX (a child or a visible attribute). */
function visibleAttr(attr: ts.JsxAttribute): boolean {
  const name = attr.name.getText();
  if (HIDDEN_ATTRS.test(name)) return false;
  const owner = attr.parent.parent;
  return !(ts.isJsxOpeningElement(owner) || ts.isJsxSelfClosingElement(owner)) || !HIDDEN_BY_COMPONENT[tagName(owner)]?.has(name);
}

function inJsx(node: ts.Node): boolean {
  const p = node.parent;
  if (ts.isJsxText(node)) return true;
  if (ts.isJsxAttribute(p)) return visibleAttr(p);
  if (ts.isJsxExpression(p)) {
    const pp = p.parent;
    if (ts.isJsxAttribute(pp)) return visibleAttr(pp);
    return ts.isJsxElement(pp) || ts.isJsxFragment(pp);
  }
  return false;
}

export function scanSource(file: string, source: string): ProseFinding[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: ProseFinding[] = [];
  const visit = (node: ts.Node) => {
    const raw = ts.isJsxText(node) ? node.text : literalText(node);
    if (raw !== null && inJsx(node)) {
      const text = normal(raw);
      if (text.length >= PROSE_MIN && alwaysShown(node)) {
        out.push({ file, line: sf.getLineAndCharacterOfPosition(node.getStart()).line + 1, text });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

const IMPORT = /(?:import|export)\s[^"'`]*?from\s*["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)/g;

function resolveImport(fromFile: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null;
  const base = resolve(dirname(fromFile), spec);
  for (const c of [base + ".tsx", base + ".ts", join(base, "index.tsx"), join(base, "index.ts"), base]) {
    if (/\.(tsx?|ts)$/.test(c) && existsSync(c)) return c;
  }
  return null;
}

/** The files a user reaches with ui_v2 ON (see the header), relative to `srcDir`. */
export function v2Files(srcDir: string): string[] {
  const pagesV2 = join(srcDir, "shell/pagesV2.ts");
  const routes = join(srcDir, "routes.ts");
  const shell = join(srcDir, "shell/AppShellV2.tsx");
  const v2Src = readFileSync(pagesV2, "utf8");
  const v2Keys = new Set([...v2Src.matchAll(/^\s+"?([\w-]+)"?:\s*lazy\(/gm)].map((m) => m[1]));
  const entries: string[] = [];
  for (const m of v2Src.matchAll(/import\(\s*["']([^"']+)["']\s*\)/g)) {
    const f = resolveImport(pagesV2, m[1]); if (f) entries.push(f);
  }
  for (const m of readFileSync(routes, "utf8").matchAll(/^\s+"?([\w-]+)"?:\s*\(\)\s*=>\s*import\(\s*["']([^"']+)["']\s*\)/gm)) {
    if (v2Keys.has(m[1])) continue;
    const f = resolveImport(routes, m[2]); if (f) entries.push(f);
  }
  entries.push(shell);
  const seen = new Set<string>();
  const stack = [...entries];
  while (stack.length) {
    const f = stack.pop()!;
    if (seen.has(f)) continue;
    seen.add(f);
    if (f === routes || f === pagesV2) continue; // the page maps: their pages are taken one by one above
    const src = readFileSync(f, "utf8");
    for (const m of src.matchAll(IMPORT)) {
      const next = resolveImport(f, m[1] ?? m[2]);
      if (next && !seen.has(next)) stack.push(next);
    }
  }
  return [...seen].filter((f) => f.endsWith(".tsx") && !/\.(test|stories)\.tsx$/.test(f)).map((f) => relative(srcDir, f)).sort();
}

/** Pages in the v2 import closure that no user reaches today, skipped by the owner's rule (PROSE-1 item 3). */
export const UNREACHED: Readonly<Record<string, string>> = {
  "pages/MarketplacePage.tsx": "flag `marketplace` OFF - the marketplace is the last phase (CLAUDE.md §2)",
};

export function scanV2(srcDir: string): ProseFinding[] {
  return v2Files(srcDir).filter((f) => !(f in UNREACHED)).flatMap((f) => scanSource(f, readFileSync(join(srcDir, f), "utf8")));
}

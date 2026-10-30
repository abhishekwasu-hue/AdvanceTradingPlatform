import { readFileSync } from "node:fs";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import TopBarV2 from "../shell/TopBarV2";
import { PageHelpProvider } from "../shell/pageHelp";
import { PageTitleV2 } from "../trading/PartsV2";
import { PageHeader } from "./PageHeader";
import { scanSource, scanV2 } from "./proseScan";

/**
 * PROSE-1 (owner 30 Oct, docs/reviews/PROSE_INVENTORY.md): no theory / explanation text is shown on a ui_v2 page every
 * time it opens. The guard runs the inventory's AST rule (src/ui/proseScan.ts) over every file a user reaches with
 * ui_v2 ON; ALLOWED holds the owner's item 4 only - each entry names its kind and the start of its text.
 */
type Allowed = "error" | "empty" | "confirm" | "status" | "sebi";
const ALLOWED: ReadonlyArray<{ file: string; starts: string; kind: Allowed }> = [
];

const allowed = (f: { file: string; text: string }) => ALLOWED.some((a) => a.file === f.file && f.text.startsWith(a.starts));

describe("PROSE-1: the AST rule", () => {
  it("counts long JSX text and visible props; skips Details, conditions, messages and hidden attributes", () => {
    const long = "This sentence explains how the page works and is far longer than sixty characters.";
    const src = `
      export function A({ on }: { on: boolean }) {
        if (!on) return <p>${long}</p>;
        return (<div>
          <p>${long}</p>
          <Card description="${long}" />
          <Details id="x"><p>${long}</p></Details>
          {on && <p>${long}</p>}
          {on ? <p>${long}</p> : null}
          <input placeholder="${long}" title="${long}" />
          <PageHeader title="T" line="${long}" />
          <button onClick={() => setError("${long}")}>Go</button>
          <p>short line</p>
        </div>);
      }`;
    expect(scanSource("a.tsx", src).map((f) => f.line)).toEqual([5, 6]);
  });
});

describe("PROSE-1: the page subtitle sits behind the title's (?)", () => {
  const SUB = "How much you can lose today, and the rules every entry is checked against.";

  it("outside the v2 shell the headers draw it as before", () => {
    expect(renderToStaticMarkup(<PageTitleV2 title="Risk limits" subtitle={SUB} />)).toContain(SUB);
    expect(renderToStaticMarkup(<PageHeader title="Risk limits" line={SUB} />)).toContain(SUB);
  });

  it("inside the shell the page draws no subtitle; the top bar's one (?) holds it, closed by default", () => {
    const inShell = (node: ReactNode, subtitle: ReactNode = null) => renderToStaticMarkup(
      <MemoryRouter>
        <PageHelpProvider initialSubtitle={subtitle}>
          <TopBarV2 title="Risk limits" user={null} notificationsActive={false} accountActive={false} accountPath="/login" onSearch={() => {}} />
          {node}
        </PageHelpProvider>
      </MemoryRouter>);
    expect(inShell(<PageTitleV2 title="Risk limits" subtitle={SUB} />)).not.toContain(SUB);
    expect(inShell(<PageHeader title="Risk limits" line={<>{SUB}</>} />)).not.toContain(SUB);
    const bar = inShell(null, SUB);
    expect(bar.match(/data-help-button/g)).toHaveLength(1);
    expect(bar).not.toContain(SUB);                                     // behind the (?) until it is opened
    for (const f of ["src/trading/PartsV2.tsx", "src/ui/PageHeader.tsx", "src/components/primitives/PageHeader.tsx"]) {
      expect(readFileSync(f, "utf8")).toMatch(/useRegisterPageSubtitle\(inShell \?/);
    }
  });
});

describe("PROSE-1: the guard over every ui_v2 page", () => {
  const findings = scanV2(`${process.cwd()}/src`);

  it("no always-visible explanation outside Details (item 4 allowlist only)", () => {
    const left = findings.filter((f) => !allowed(f)).map((f) => `${f.file}:${f.line} ${f.text.slice(0, 90)}`);
    expect(left).toEqual([]);
  });

  it("every allowlist entry still matches a real line (no stale entries)", () => {
    expect(ALLOWED.filter((a) => !findings.some((f) => f.file === a.file && f.text.startsWith(a.starts)))).toEqual([]);
  });
});

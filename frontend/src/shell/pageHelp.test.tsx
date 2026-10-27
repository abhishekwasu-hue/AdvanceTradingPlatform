import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import TopBarV2 from "./TopBarV2";
import { PageHelpProvider, type PageHelp } from "./pageHelp";

/** UX-6: a page's help line sits behind the (?) beside the title in the header, never as a card above the data. */
const bar = (help: PageHelp | null, onTour?: () => void) => renderToStaticMarkup(
  <MemoryRouter>
    <PageHelpProvider initial={help}>
      <TopBarV2 title="Charts" user={null} notificationsActive={false} accountActive={false} accountPath="/login" onSearch={() => {}} onTour={onTour} />
    </PageHelpProvider>
  </MemoryRouter>);

describe("UX-6: page help in the header (?)", () => {
  it("a page with help: one (?) labelled with the page title; the line is not printed in the bar itself", () => {
    const html = bar({ id: "charts", lines: ["Search a symbol at the top left."] });
    expect(html).toContain('aria-label="Help: Charts"');
    expect(html).toContain('data-help="Charts"');
    expect(html).not.toContain("Search a symbol at the top left.");                  // behind the (?) until opened
    expect(html.match(/data-help-button/g)).toHaveLength(1);
  });

  it("the page tips move into the same (?): no second question-mark button beside it", () => {
    const tour = () => {};
    expect(bar({ id: "charts", lines: ["a"] }, tour)).not.toContain('aria-label="Show the tips for this page"');
    expect(bar(null, tour)).toContain('aria-label="Show the tips for this page"');   // no help line: the tips button as before
    expect(bar(null)).not.toContain("data-help-button");
  });

  it("inside the shell PageIntro hands its line to the header and draws nothing in the page", () => {
    const intro = readFileSync("src/ui/PageIntro.tsx", "utf8");
    expect(intro).toMatch(/useRegisterPageHelp\(inShell \?/);
    expect(intro).toMatch(/if \(inShell \|\| userId === null/);
    const shell = readFileSync("src/shell/AppShellV2.tsx", "utf8");
    expect(shell).toMatch(/<PageHelpProvider>[\s\S]*<TopBarV2[\s\S]*<\/PageHelpProvider>/);
  });
});

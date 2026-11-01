import { useEffect, useMemo, type ReactNode } from "react";
import { MemoryRouter, Route, Routes, UNSAFE_LocationContext, useLocation, useNavigate, useNavigationType } from "react-router";

/**
 * M75 NAV-2: each panel's page gets its own address (docs/design/M75_NO_NAVIGATION.md section 4.1: the existing page
 * component is reused inside the panel). A page reads its search and its handed-over state (a prompt, a draft id, an
 * idea) from its own location, exactly as on its full route, and its own address changes (a filter in the search, a
 * clean-up of the handed state) stay inside the panel - they never touch the tab's address, so the panel and the tab
 * underneath are never closed or replaced by them.
 * A page in the panel that goes to ANOTHER address (a link or a button inside it) is handed to the host (`onLeave`):
 * the host opens that facility in place on top, or navigates there as today when no panel shows it. A clean-up to
 * another address (`replace`) is not a click and is dropped.
 * The tab's router is hidden from the panel (its location context is reset) so the panel's own router can sit inside it;
 * the tab's address is still written only by the host.
 */
export function PanelRouter({ path, state, onLeave, children }: {
  /** The full route this panel shows (`/strategies/build`, `/signals?proposal=7`). */
  path: string;
  /** What the opening button handed over (as navigation state). */
  state?: unknown;
  onLeave: (to: string, state: unknown) => void;
  children: ReactNode;
}) {
  const home = useMemo(() => {
    const u = new URL(path, "http://app.invalid");
    return { pathname: u.pathname, search: u.search, hash: u.hash, state: state ?? null };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- the panel's address is fixed when it opens
  return (
    <UNSAFE_LocationContext.Provider value={null as never}>
      <MemoryRouter initialEntries={[home]}>
        <Routes>
          <Route path="*" element={<><Leave home={home} onLeave={onLeave} />{children}</>} />
        </Routes>
      </MemoryRouter>
    </UNSAFE_LocationContext.Provider>
  );
}

type Home = { pathname: string; search: string; hash: string; state: unknown };

function Leave({ home, onLeave }: { home: Home; onLeave: (to: string, state: unknown) => void }) {
  const location = useLocation();
  const type = useNavigationType();
  const navigate = useNavigate();
  useEffect(() => {
    if (location.pathname === home.pathname) return;
    // The panel stays on its own facility: back off the pushed entry (a click), or put the panel's address back (a
    // clean-up, dropped).
    if (type === "PUSH") {
      navigate(-1);
      onLeave(`${location.pathname}${location.search}${location.hash}`, location.state);
    } else navigate({ pathname: home.pathname, search: home.search, hash: home.hash }, { replace: true, state: home.state });
  }, [location]); // eslint-disable-line react-hooks/exhaustive-deps -- only a new address matters
  return null;
}

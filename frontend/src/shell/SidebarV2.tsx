import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import Logo from "../components/Logo";
import { PAGES, preloadPage } from "../routes";
import type { Page } from "../components/Sidebar";
import type { UserResponse } from "../types";
import { StatusDot } from "../broker/marks";
import StatusLink from "../status/StatusLink";
import type { BrokerDot } from "../broker/brokerState";
import { NAV_PINNED_V2, isCurrentEntry, navDotFor, type NavItemV2, type NavSectionV2, type ShellKey } from "./navModel";
import { navIconClass } from "./navTone";
import type { SidebarMode } from "./sidebarPrefs";
import { SectionChip } from "../ui/SectionChip";
import { SECTION_TINT, navWordClass, sectionTintOf } from "../ui/accents";
import { useColourV2 } from "../ui/colourV2";

/**
 * UI2-1 (UI v2 proposal §2), M60 (sidebar / tabs, owner order approved 30 Oct) and M57 (show / hide, owner order 30 Oct):
 * the desktop and tablet sidebar (md and up; phones use the bottom bar, the More sheet and the top bar's menu button).
 * Pages whose feature is off are not listed at all (the caller passes `visibleSections(...)`). A merged page's tab or a
 * Settings page lights up its entry (isCurrentEntry).
 */

/** "SUPER_ADMIN" -> "Platform operator", "OWNER" -> "Owner". */
export function roleLabel(role: string): string {
  if (role === "SUPER_ADMIN") return "Platform operator";
  const words = role.toLowerCase().split("_").filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

/** Owner's user-friendly pass (23 Oct, item 3): the role under the name only in an organisation of more than one
 * member - a trader on his own is not told he is the "Owner". Unknown (not read yet, or the read failed): hidden. */
export function showRoleFor(members: number | null | undefined): boolean {
  return typeof members === "number" && members > 1;
}

/** How many active members this organisation has (`GET /api/team/tenant` -> `members`); null until known. */
export function useOrgMembers(userId: number | null): number | null {
  const [members, setMembers] = useState<number | null>(null);
  useEffect(() => {
    setMembers(null);
    if (userId === null) return;
    let current = true;
    api.getTenant().then((t) => { if (current) setMembers(typeof t?.members === "number" ? t.members : null); }, () => { if (current) setMembers(null); });
    return () => { current = false; };
  }, [userId]);
  return members;
}

/** Two letters for the avatar, from the email's name part ("abhi.wasu@..." -> "AW"). */
export function initialsOf(email: string): string {
  const name = email.split("@")[0] ?? "";
  const parts = name.split(/[._\-+\s]+/).filter(Boolean);
  const letters = parts.length >= 2 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return letters.toUpperCase() || "?";
}

export interface SidebarV2Props {
  sections: NavSectionV2[];
  /** The page on screen (its entry is marked current). */
  current: ShellKey | null;
  user: UserResponse | null;
  /** Where the account footer goes (Settings -> Account while signed in, the sign-in page otherwise). */
  accountPath: string;
  /** P2b: entries above the sections (none since M60; kept for callers that pass them). */
  pinned?: readonly NavItemV2[];
  /** The broker's status dot - on the Settings row since M60 (null: none). */
  dot?: BrokerDot | null;
  /** Active members of the organisation (null: not known): the role shows only above one (`showRoleFor`). */
  members?: number | null;
  /** M57 U7-1: the full menu, or the 56 px rail of icons (md and up). */
  mode?: SidebarMode;
}

function NavLink({ item, current, dot, rail }: { item: NavItemV2; current: ShellKey | null; dot: BrokerDot | null; rail: boolean }) {
  const Icon = item.icon;
  const active = isCurrentEntry(item.key, current);
  const colour = useColourV2();
  const preload = isPage(item.key) ? () => preloadPage(item.key as Page) : undefined;
  return (
    <Link to={item.path} aria-current={active ? "page" : undefined} onMouseEnter={preload} onFocus={preload}
      // M57 U7-1: on the rail each icon keeps its name as a tooltip and as the link's accessible name.
      title={rail ? item.label : undefined} data-nav-key={item.key}
      className={`flex h-9 items-center rounded-control [@media(pointer:coarse)]:h-11 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
        rail ? "relative justify-center px-0" : "gap-space-3 px-space-2"} ${
        active ? "bg-brand/10 font-semibold text-fg" : "font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
      }`}>
      {/* UI-H CA3: with colour v2 the icon sits in its section's round tint chip (the row's own fill marks the page on screen). */}
      {colour && sectionTintOf(item.key) ? <SectionChip navKey={item.key} icon={Icon} /> : <Icon size={16} aria-hidden className={active ? "text-brand" : navIconClass(item.key, colour)} />}
      <span className={rail ? "sr-only" : "truncate"}>{item.label}</span>
      {dot && <StatusDot dot={dot} announce className={rail ? "absolute right-space-1 top-space-1 ring-2 ring-surface-1" : "ml-auto"} />}
    </Link>
  );
}

/** Only routed pages have a chunk to preload (Charts, Ideas, Sectors & indices and the Settings sections are v2 pages). */
const isPage = (key: ShellKey): boolean => (PAGES as string[]).includes(key) && key !== "charts";

/**
 * M60 (owner order, approved 30 Oct) + M57 (show / hide): the desktop and tablet sidebar - five short sections (Trade,
 * Find, Build, Learn & AI, Account), each title in its section's accent, with Admin at the end of Account for the
 * platform operator only. The broker's connection dot sits on the Settings row (Broker accounts live in Settings). As a
 * rail (M57) it is 56 px of icons, each with its name as a tooltip; a thin line stands for each section title.
 */
export function SidebarV2View({ sections, current, user, accountPath, pinned = NAV_PINNED_V2, dot = null, members = null, mode = "expanded" }: SidebarV2Props) {
  const rail = mode === "rail";
  const colour = useColourV2();
  return (
    <aside aria-label="Navigation" data-sidebar-mode={mode}
      className={`hidden shrink-0 flex-col border-r border-subtle bg-surface-1 transition-[width] duration-fast motion-reduce:transition-none md:sticky md:top-0 md:flex md:h-screen ${rail ? "w-14" : "w-60"}`}>
      <div className={`flex h-14 shrink-0 items-center border-b border-subtle ${rail ? "justify-center" : "px-space-4"}`}>
        <Link to="/" aria-label="AiAlgoDesk - Today" title={rail ? "AiAlgoDesk" : undefined}
          className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <Logo size={28} showWordmark={!rail} />
        </Link>
      </div>
      <nav aria-label="Pages" data-tour="nav-sidebar" className={`flex-1 overflow-y-auto py-space-3 ${rail ? "px-space-1" : "px-space-2"}`}>
        {pinned.length > 0 && (
          <ul className="mb-space-2 space-y-px">
            {pinned.map((item) => <li key={item.key} data-nav-pinned><NavLink item={item} current={current} dot={navDotFor(item.key, dot)} rail={rail} /></li>)}
          </ul>
        )}
        {sections.map((section, at) => {
          const headingId = `nav-section-${section.id}`;
          const tint = SECTION_TINT[section.id];
          return (
            <section key={section.id} aria-labelledby={headingId} data-nav-section={section.id} className={at > 0 ? "mt-space-3" : undefined}>
              {/* The section title: visible on the full menu, read out on the rail (a thin line stands for it there). */}
              <h2 id={headingId}
                className={rail ? "sr-only" : `px-space-2 pb-space-1 text-t12 font-semibold uppercase tracking-wide ${colour ? navWordClass(tint) : "text-fg-muted"}`}>
                {section.title}
              </h2>
              {rail && at > 0 && <span aria-hidden className="mx-space-2 mb-space-2 block border-t border-subtle" />}
              <ul className="space-y-px" data-nav-list>
                {section.items.map((item) => (
                  <li key={item.key} data-nav-admin={item.role ? true : undefined}>
                    <NavLink item={item} current={current} dot={navDotFor(item.key, dot)} rail={rail} />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </nav>
      {/* A2: the footer's Status link (hidden while there is no status page to link to). */}
      {!rail && <div className="shrink-0 border-t border-subtle px-space-4 empty:hidden" data-sidebar-footer><StatusLink /></div>}
      <Link to={accountPath} aria-current={current === "account" ? "page" : undefined} title={rail ? (user ? user.email : "Sign in") : undefined}
        aria-label={rail ? (user ? `Account, ${user.email}` : "Sign in") : undefined}
        className={`flex shrink-0 items-center border-t border-subtle py-space-3 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand ${rail ? "justify-center" : "gap-space-3 px-space-4"}`}>
        <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center rounded-pill bg-surface-2 text-t12 font-semibold text-fg">
          {user ? initialsOf(user.email) : "?"}
        </span>
        {!rail && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-t13 font-semibold text-fg">{user ? user.email : "Sign in"}</span>
              {user && showRoleFor(members) && <span className="block truncate text-t12 text-fg-muted" data-role-label>{roleLabel(user.role)}</span>}
            </span>
            <span className="shrink-0 rounded-pill border border-border px-space-2 py-px text-t12 font-medium text-fg-muted">Paper</span>
          </>
        )}
      </Link>
    </aside>
  );
}

export default function SidebarV2(props: SidebarV2Props) {
  const members = useOrgMembers(props.user?.id ?? null);
  return <SidebarV2View members={members} {...props} />;
}

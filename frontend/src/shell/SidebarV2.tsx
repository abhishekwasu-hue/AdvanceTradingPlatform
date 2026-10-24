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
import { NAV_PINNED_V2, navDotFor, navEntryFor, type NavItemV2, type NavSectionV2, type ShellKey } from "./navModel";

/**
 * UI2-1 (UI v2 proposal §2, mockup nav-sidebar-1440), UX3-9 (UX audit section 5 "The proposed sidebar"): the desktop
 * and tablet sidebar - the 13 entries (Today first; day-one fix 9 puts Broker, with its status dot, just above Settings) in task order in one flat list (no section
 * headings, nothing folds away), and the Admin console set apart at the bottom for the platform operator only. Pages
 * whose feature is off are not listed at all (the caller passes `visibleSections(...)`). A merged page's tab or a
 * Settings sub-page lights up its entry (navEntryFor). Phones use the bottom bar and the More sheet instead (md and up
 * only).
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
  /** P2b: the entries above the list (Broker), always shown. */
  pinned?: readonly NavItemV2[];
  /** P2b: the broker's status dot on the Broker entry (null: none). */
  dot?: BrokerDot | null;
  /** Active members of the organisation (null: not known): the role shows only above one (`showRoleFor`). */
  members?: number | null;
}

function NavLink({ item, current, dot }: { item: NavItemV2; current: ShellKey | null; dot: BrokerDot | null }) {
  const Icon = item.icon;
  const active = current === item.key;
  const preload = isPage(item.key) ? () => preloadPage(item.key as Page) : undefined;
  return (
    <Link to={item.path} aria-current={active ? "page" : undefined} onMouseEnter={preload} onFocus={preload}
      className={`flex h-9 items-center gap-space-3 rounded-control [@media(pointer:coarse)]:h-11 px-space-2 text-t14 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
        active ? "bg-brand/10 font-semibold text-fg" : "font-medium text-fg-muted hover:bg-surface-2 hover:text-fg"
      }`}>
      <Icon size={16} aria-hidden className={active ? "text-brand" : "text-fg-muted"} />
      <span className="truncate">{item.label}</span>
      {dot && <StatusDot dot={dot} announce className="ml-auto" />}
    </Link>
  );
}

/** Only routed pages have a chunk to preload (Charts, Ideas, Sectors & indices and the Settings sections are v2 pages). */
const isPage = (key: ShellKey): boolean => (PAGES as string[]).includes(key) && key !== "charts";

export function SidebarV2View({ sections, current, user, accountPath, pinned = NAV_PINNED_V2, dot = null, members = null }: SidebarV2Props) {
  const entry = navEntryFor(current);
  const userItems = sections.filter((s) => s.id !== "admin").flatMap((s) => s.items);
  const adminItems = sections.filter((s) => s.id === "admin").flatMap((s) => s.items);
  return (
    <aside aria-label="Navigation"
      className="hidden w-60 shrink-0 flex-col border-r border-subtle bg-surface-1 md:sticky md:top-0 md:flex md:h-screen">
      <div className="flex h-14 shrink-0 items-center border-b border-subtle px-space-4">
        <Link to="/" aria-label="AiAlgoDesk - Today" className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <Logo size={28} />
        </Link>
      </div>
      <nav aria-label="Pages" data-tour="nav-sidebar" className="flex-1 overflow-y-auto px-space-2 py-space-3">
        <ul className="space-y-px" data-nav-list>
          {/* Day-one fix 9: Today first; Broker (always shown, with its dot) sits just above Settings. */}
          {userItems.filter((i) => i.key !== "settings").map((item) => <li key={item.key}><NavLink item={item} current={entry} dot={navDotFor(item.key, dot)} /></li>)}
          {pinned.map((item) => (
            <li key={item.key} data-nav-pinned><NavLink item={item} current={entry} dot={navDotFor(item.key, dot)} /></li>
          ))}
          {userItems.filter((i) => i.key === "settings").map((item) => <li key={item.key}><NavLink item={item} current={entry} dot={null} /></li>)}
        </ul>
        {adminItems.length > 0 && (
          <div className="mt-space-3 border-t border-subtle pt-space-3" data-nav-admin>
            <p className="px-space-2 pb-space-1 text-t12 font-semibold text-fg-muted">Admin</p>
            <ul className="space-y-px">
              {adminItems.map((item) => <li key={item.key}><NavLink item={item} current={entry} dot={null} /></li>)}
            </ul>
          </div>
        )}
      </nav>
      {/* A2: the footer's Status link (hidden while there is no status page to link to). */}
      <div className="shrink-0 border-t border-subtle px-space-4 empty:hidden" data-sidebar-footer><StatusLink /></div>
      <Link to={accountPath} aria-current={current === "account" ? "page" : undefined}
        className="flex shrink-0 items-center gap-space-3 border-t border-subtle px-space-4 py-space-3 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand">
        <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center rounded-pill bg-surface-2 text-t12 font-semibold text-fg">
          {user ? initialsOf(user.email) : "?"}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-t13 font-semibold text-fg">{user ? user.email : "Sign in"}</span>
          {user && showRoleFor(members) && <span className="block truncate text-t12 text-fg-muted" data-role-label>{roleLabel(user.role)}</span>}
        </span>
        <span className="shrink-0 rounded-pill border border-border px-space-2 py-px text-t12 font-medium text-fg-muted">Paper</span>
      </Link>
    </aside>
  );
}

export default function SidebarV2(props: SidebarV2Props) {
  const members = useOrgMembers(props.user?.id ?? null);
  return <SidebarV2View members={members} {...props} />;
}

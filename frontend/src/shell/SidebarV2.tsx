import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import Logo from "../components/Logo";
import { preloadPage } from "../routes";
import type { Page } from "../components/Sidebar";
import type { UserResponse } from "../types";
import { StatusDot } from "../broker/marks";
import type { BrokerDot } from "../broker/brokerState";
import { NAV_PINNED_V2, navDotFor, type NavItemV2, type NavSectionV2, type SectionId, type ShellKey } from "./navModel";

/**
 * UI2-1 (UI v2 proposal §2, mockup nav-sidebar-1440): the desktop and tablet sidebar - five collapsible sections
 * (Markets, Strategies, Trading, Insights, Account) and More. Markets and Trading start open, the rest start closed;
 * each section remembers the trader's choice (this browser), and the section of the page on screen opens itself.
 * Pages whose feature is off, and the Admin console for anyone but the platform operator, are not listed at all (the
 * caller passes `visibleSections(...)`). Phones use the bottom bar and the More sheet instead (from md up only).
 */

export const SECTIONS_KEY = "atp_nav_sections";
export const DEFAULT_OPEN: Readonly<Record<SectionId, boolean>> = {
  markets: true, strategies: false, trading: true, insights: false, account: false, more: false,
};
export type SectionMemory = Partial<Record<SectionId, boolean>>;

/** The stored choices; anything unreadable is ignored (back to the defaults). */
export function parseSectionMemory(raw: string | null): SectionMemory {
  try {
    const parsed: unknown = JSON.parse(raw ?? "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: SectionMemory = {};
    for (const id of Object.keys(DEFAULT_OPEN) as SectionId[]) {
      const v = (parsed as Record<string, unknown>)[id];
      if (typeof v === "boolean") out[id] = v;
    }
    return out;
  } catch {
    return {};
  }
}

/** True when section `id` is open: the trader's choice, else the default. */
export function isSectionOpen(memory: SectionMemory, id: SectionId): boolean {
  return memory[id] ?? DEFAULT_OPEN[id];
}

/** The memory after arriving on a page in `current`: that section opens (and stays open until closed again). */
export function openForCurrent(memory: SectionMemory, current: SectionId | null): SectionMemory {
  if (current === null || isSectionOpen(memory, current)) return memory;
  return { ...memory, [current]: true };
}

function readMemory(): SectionMemory {
  try { return parseSectionMemory(localStorage.getItem(SECTIONS_KEY)); } catch { return {}; }
}
function writeMemory(m: SectionMemory) {
  try { localStorage.setItem(SECTIONS_KEY, JSON.stringify(m)); } catch { /* storage unavailable: this visit only */ }
}

/** The open / closed sections, remembered per browser; the current page's section opens itself. */
export function useSectionMemory(current: SectionId | null): [SectionMemory, (id: SectionId) => void] {
  const [memory, setMemory] = useState<SectionMemory>(() => openForCurrent(readMemory(), current));
  useEffect(() => {
    setMemory((m) => {
      const next = openForCurrent(m, current);
      if (next !== m) writeMemory(next);
      return next;
    });
  }, [current]);
  const toggle = useCallback((id: SectionId) => {
    setMemory((m) => {
      const next = { ...m, [id]: !isSectionOpen(m, id) };
      writeMemory(next);
      return next;
    });
  }, []);
  return [memory, toggle];
}

/** "SUPER_ADMIN" -> "Platform operator", "OWNER" -> "Owner". */
export function roleLabel(role: string): string {
  if (role === "SUPER_ADMIN") return "Platform operator";
  const words = role.toLowerCase().split("_").filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : "";
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
  current: ShellKey | null;
  memory: SectionMemory;
  onToggle: (id: SectionId) => void;
  user: UserResponse | null;
  /** Where the account footer goes (Settings -> Account while signed in, the sign-in page otherwise). */
  accountPath: string;
  /** P2b: the entries above the sections (Broker), always shown. */
  pinned?: readonly NavItemV2[];
  /** P2b: the broker's status dot on the Broker entry (null: none). */
  dot?: BrokerDot | null;
}

function NavLink({ item, current, dot }: { item: NavItemV2; current: ShellKey | null; dot: BrokerDot | null }) {
  const Icon = item.icon;
  const active = current === item.key;
  const preload = item.key === "charts" ? undefined : () => preloadPage(item.key as Page);
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

export function SidebarV2View({ sections, current, memory, onToggle, user, accountPath, pinned = NAV_PINNED_V2, dot = null }: SidebarV2Props) {
  return (
    <aside aria-label="Navigation"
      className="hidden w-60 shrink-0 flex-col border-r border-subtle bg-surface-1 md:sticky md:top-0 md:flex md:h-screen">
      <div className="flex h-14 shrink-0 items-center border-b border-subtle px-space-4">
        <Link to="/" aria-label="AiAlgoDesk - Dashboard" className="rounded-control focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          <Logo size={28} />
        </Link>
      </div>
      <nav aria-label="Pages" className="flex-1 overflow-y-auto px-space-2 py-space-3">
        {pinned.length > 0 && (
          <ul className="mb-space-2 space-y-px" data-nav-pinned>
            {pinned.map((item) => <li key={item.key}><NavLink item={item} current={current} dot={navDotFor(item.key, dot)} /></li>)}
          </ul>
        )}
        {sections.map((section) => {
          const open = isSectionOpen(memory, section.id);
          const listId = `nav-section-${section.id}`;
          return (
            <div key={section.id} className="mb-space-1">
              <button type="button" onClick={() => onToggle(section.id)} aria-expanded={open} aria-controls={listId}
                className="flex h-9 w-full items-center justify-between rounded-control [@media(pointer:coarse)]:h-11 px-space-2 text-t12 font-semibold text-fg-muted hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
                {section.title}
                {open ? <ChevronDown size={14} aria-hidden /> : <ChevronRight size={14} aria-hidden />}
              </button>
              {open && (
                <ul id={listId} className="mb-space-2 mt-space-1 space-y-px">
                  {section.items.map((item) => (
                    <li key={item.key}><NavLink item={item} current={current} dot={navDotFor(item.key, dot)} /></li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </nav>
      <Link to={accountPath} aria-current={current === "account" ? "page" : undefined}
        className="flex shrink-0 items-center gap-space-3 border-t border-subtle px-space-4 py-space-3 hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand">
        <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center rounded-pill bg-surface-2 text-t12 font-semibold text-fg">
          {user ? initialsOf(user.email) : "?"}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-t13 font-semibold text-fg">{user ? user.email : "Sign in"}</span>
          {user && <span className="block truncate text-t12 text-fg-muted">{roleLabel(user.role)}</span>}
        </span>
        <span className="shrink-0 rounded-pill border border-border px-space-2 py-px text-t12 font-medium text-fg-muted">Paper</span>
      </Link>
    </aside>
  );
}

export default function SidebarV2(props: Omit<SidebarV2Props, "memory" | "onToggle"> & { currentSection: SectionId | null }) {
  const { currentSection, ...rest } = props;
  const [memory, toggle] = useSectionMemory(currentSection);
  return <SidebarV2View {...rest} memory={memory} onToggle={toggle} />;
}

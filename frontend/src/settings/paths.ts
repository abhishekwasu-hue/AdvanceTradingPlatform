/**
 * Settings redesign part B ("Settings = a short index with status", docs/design/SETTINGS_REDESIGN_REVIEW.md B1-B4):
 * the addresses of the Settings section pages (v2 only). A tiny module on purpose: App.tsx reads it with `ui_v2` off
 * too (shell/aliases.ts flagOffFallback), so it must not pull the v2 Settings pages into the first download.
 *
 * Account & plan stays at `/settings/account`, the setup wizard at `/settings/setup` and the Broker at `/broker`.
 */
export type SettingsSectionId = "ai" | "alerts" | "appearance" | "help" | "advanced" | "risk-limits";

/** The v2 shell location of each section page. */
export type SettingsSectionKey = `settings-${SettingsSectionId}`;

export const SETTINGS_INDEX = "/settings";

export const SETTINGS_SECTIONS: readonly { id: SettingsSectionId; key: SettingsSectionKey; path: string; old: string }[] = [
  // `old`: where the address goes with `ui_v2` off - today's Settings page, at the part that holds it.
  { id: "ai", key: "settings-ai", path: "/settings/ai", old: "/settings#ai" },
  { id: "alerts", key: "settings-alerts", path: "/settings/alerts", old: "/settings#alerts" },
  { id: "appearance", key: "settings-appearance", path: "/settings/appearance", old: "/settings" },
  { id: "help", key: "settings-help", path: "/settings/help", old: "/settings" },
  { id: "advanced", key: "settings-advanced", path: "/settings/advanced", old: "/settings#integrations" },
  // M64: Settings › Risk limits - every order-time risk check a switch (off by default); with ui_v2 off, today's Risk page.
  { id: "risk-limits", key: "settings-risk-limits", path: "/settings/risk-limits", old: "/risk-management" },
];

export const SETTINGS_PATH: Readonly<Record<SettingsSectionId, string>> =
  Object.fromEntries(SETTINGS_SECTIONS.map((s) => [s.id, s.path])) as Record<SettingsSectionId, string>;

/** The section page at `cleanPath` (no trailing slash), or undefined. */
export function settingsSectionAt(cleanPath: string): (typeof SETTINGS_SECTIONS)[number] | undefined {
  return SETTINGS_SECTIONS.find((s) => s.path === cleanPath);
}

/** Where the Go-live checklist is in v2 (Settings → Advanced), shown only while `phase0_paper_only` is off. */
export const GO_LIVE_PATH_V2 = "/settings/advanced#go-live";

/**
 * B4: the old `/settings#<part>` links, on the v2 Settings index (the shell's own redirect never sees the hash, so the
 * index forwards them). `liveHidden` (Phase 0 PAPER only, SR-9): `#go-live` opens the index itself, nothing about LIVE.
 * Null: stay on the index.
 */
export function settingsHashTarget(hash: string, liveHidden: boolean): string | null {
  switch (hash.toLowerCase()) {
    case "#ai": return SETTINGS_PATH.ai;
    case "#alerts": return SETTINGS_PATH.alerts;
    case "#integrations": return `${SETTINGS_PATH.advanced}#webhook`;
    case "#advanced": return SETTINGS_PATH.advanced;
    case "#appearance": return SETTINGS_PATH.appearance;
    case "#help": return SETTINGS_PATH.help;
    case "#go-live": return liveHidden ? SETTINGS_INDEX : GO_LIVE_PATH_V2;
    default: return null;
  }
}

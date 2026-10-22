/** MO4 (backend app/rhythm): the daily rhythm's digests and the user's Telegram choice, as `/api/rhythm/*` sends them. */

export type RhythmKind = "pre_market" | "mid_session" | "closing" | "weekly";

export interface RhythmSection {
  id: string;
  title: string;
  /** Plain sentences built from the user's own data and the market's; empty when there is nothing to say. */
  lines: string[];
  /** What to say when `lines` is empty (never a sample figure). */
  empty: string | null;
  /** The page this section opens. */
  link: { label: string; to: string } | null;
}

export interface RhythmBrief {
  kind: RhythmKind;
  title: string;
  subtitle: string;
  as_of: string;
  sections: RhythmSection[];
  has_content: boolean;
  note: string;
}

export interface RhythmToday {
  /** The digest of the moment on the IST market calendar. */
  phase: RhythmKind;
  kinds: RhythmKind[];
  brief: RhythmBrief;
}

export interface RhythmPrefs {
  telegram: boolean;
  pre_market: boolean;
  mid_session: boolean;
  closing: boolean;
  weekly: boolean;
}

export interface RhythmSettings {
  prefs: RhythmPrefs;
  version: number;
  telegram: { connected: boolean; reaches_you: boolean };
}

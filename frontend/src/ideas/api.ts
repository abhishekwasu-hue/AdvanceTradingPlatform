/** Idea library slice 1 (flag `idea_library`; the server answers 404 while it is off): the read-only cards. */
import { request } from "../api/client";

export interface IdeaChoice { value: number | string; label: string }
export interface IdeaSetting {
  key: string; label: string; help: string; type: "number" | "choice"; default: number | string;
  min: number | null; max: number | null; step: number | null; choices: IdeaChoice[];
}
/** A "Try as a scan" screen: ScreenQL from the registry with `$settings`, their defaults, and whether it can open now. */
export interface IdeaScan { id: string; label: string; base_tf: string; text: string; params: Record<string, number | string>; available: boolean }
export interface IdeaLogic { context: string; area: string; trigger: string; invalidation: string; evidence: string[] }
export interface Idea {
  id: string; name: string; group: string; difficulty: "Beginner" | "Intermediate"; one_line: string;
  works_best_when: string; tends_to_fail_when: string; plain: string[]; logic: IdeaLogic; structure_words: string[];
  settings: IdeaSetting[]; your_decisions: string[]; provenance: string; scans: IdeaScan[]; scan_gaps: string | null;
  adopt: IdeaAdopt;
}
/** Slice 2 (IDEA-1): an idea mapped honestly onto a strategy template the organisation can adopt now; otherwise only
 * `available: false` ("comes later"). The settings are the Trading bot form's pre-fill. Slice 3: `note` says, in plain
 * words, where the paper strategy still reads less than the card. */
export type IdeaAdopt =
  | { available: false }
  | { available: true; template: string; timeframe: string | null; params: Record<string, number | string | boolean>; note?: string | null };
export interface IdeaGroup { id: string; name: string }
export interface BeginnerOption { id: string; label: string; ideas: string[] }
export interface BeginnerQuestion { id: string; text: string; options: BeginnerOption[] }
export interface IdeaLibrary {
  header: { title: string; subtitle: string; frame: string[]; adopt_later: string };
  groups: IdeaGroup[];
  beginner: { title: string; intro: string; questions: BeginnerQuestion[] };
  ideas: Idea[];
}

export const ideasApi = {
  library: () => request<IdeaLibrary>("/ideas"),
  idea: (id: string) => request<Idea>(`/ideas/${encodeURIComponent(id)}`),
};

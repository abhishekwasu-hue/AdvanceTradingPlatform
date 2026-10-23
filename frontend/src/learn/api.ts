/** Readiness C13: the Learn section's read (`GET /api/learn`, 404 while `ui_v2` or `learn` is off). */
import { request } from "../api/client";

export interface LessonNext { label: string; path: string }
export interface Lesson {
  id: string; group: string; title: string; summary: string; points: string[];
  /** Keys of the glossary (src/ui/glossary.ts) this lesson explains. */
  glossary: string[];
  next: LessonNext | null;
  /** The operator's video link for this lesson, or null (empty by default). */
  video_url: string | null;
}
export interface LessonGroup { id: string; name: string }
export interface LearnLibrary {
  header: { title: string; subtitle: string; frame: string[] };
  groups: LessonGroup[];
  lessons: Lesson[];
  /** The operator's course link, or null. */
  course_url: string | null;
}

export const learnApi = {
  library: () => request<LearnLibrary>("/learn"),
};

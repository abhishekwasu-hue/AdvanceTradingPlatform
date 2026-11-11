/**
 * Readiness C13: the operator's Learn video links and the referral reward wording (SUPER_ADMIN + two-factor on the
 * server). Kept apart from consoleApi.ts, which stays read-only.
 */
import { request } from "../api/client";

export interface LearnSettings {
  videos: Record<string, string>;
  course_url: string;
  /** M95 F4: the front door's 60-second intro video ('' = none; the sign-in page then shows no video link). */
  intro_video_url?: string;
  /** Every lesson, so the editor can list them (GET only). */
  lessons?: { id: string; title: string }[];
}

export interface ReferralTotals { joined: number; referrers: number; links_created: number }
export interface ReferralSettings { reward_text: string; totals: ReferralTotals }

export const growthSettingsApi = {
  learn: () => request<LearnSettings>("/admin/learn/settings"),
  saveLearn: (body: LearnBody) =>
    request<LearnSettings>("/admin/learn/settings", { method: "PUT", body: JSON.stringify(body) }),
  referral: () => request<ReferralSettings>("/admin/growth/referral-settings"),
  saveReferral: (reward_text: string) =>
    request<ReferralSettings>("/admin/growth/referral-settings", { method: "PUT", body: JSON.stringify({ reward_text }) }),
};

export interface LearnBody { videos: Record<string, string>; course_url: string; intro_video_url?: string }

/** Only the filled links are sent; an empty field removes that lesson's link. The intro video (M95 F4) is sent when given. */
export function learnBody(videos: Record<string, string>, course: string, intro?: string): LearnBody {
  const out: Record<string, string> = {};
  for (const [id, url] of Object.entries(videos)) if (url.trim()) out[id] = url.trim();
  return { videos: out, course_url: course.trim(), ...(intro === undefined ? {} : { intro_video_url: intro.trim() }) };
}

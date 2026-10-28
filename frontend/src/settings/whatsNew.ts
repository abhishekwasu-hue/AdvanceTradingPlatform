/**
 * AUD-V7 (owner audit 28 Oct): "What's new" - one plain line per release, newest first. Each release is a semver tag
 * (vX.Y.Z) on main and a CHANGELOG.md section; add the line here in the same PR that adds the CHANGELOG section.
 */
export interface Release { version: string; date: string; line: string }

export const RELEASES: readonly Release[] = [
  { version: "1.0.0", date: "28 Oct 2026", line: "Your limits are your own: nothing pre-filled, and a blank limit is off." },
];

export function latestRelease(): Release | null {
  return RELEASES[0] ?? null;
}

/** Runner S7: the My runner page's address (v2 only) and its flag. A tiny module on purpose: the shell's address map
 * (shell/aliases.ts, read by App.tsx with `ui_v2` off too) imports it, so it must not pull the page or its API in. */
export const RUNNER_ADDRESS = "/runner";
export const RUNNER_FLAG = "user_runner";

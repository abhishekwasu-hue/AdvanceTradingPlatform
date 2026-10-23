/// <reference types="vite/client" />

/** PW6: the build id (package.json version + short git commit, or "dev"), injected by vite.config.ts `define`. */
declare const __APP_VERSION__: string;

/** Mobile B1: true only in the Android app's bundle (`vite build --mode android`, vite.config.ts `define`). A constant,
 * so the web build drops every branch and chunk behind it. */
declare const __ATP_NATIVE__: boolean;

/** Mobile B1: the API origin of the Android app's bundle (`https://<domain>`, or the `.invalid` placeholder of a debug
 * build; checked by scripts/mobile-rules.mjs). "" in the web build, whose API calls stay on its own origin. */
declare const __ATP_API_ORIGIN__: string;

// Item 1: the type-scale test reads the Tailwind theme (a plain JS file with no types of its own).
declare module "*/tailwind.config.js" {
  const config: { theme: { extend: Record<string, unknown> & { fontSize: Record<string, [string, { lineHeight: string }]> } } };
  export default config;
}

/** A2 (RDY-11): the footer's "Status" link goes here when set (an uptime service's hosted status page, https only);
 * unset = the in-app /status page while the operator has it on. Set at build time; not a secret. */
interface ImportMetaEnv {
  readonly VITE_STATUS_PAGE_URL?: string;
}

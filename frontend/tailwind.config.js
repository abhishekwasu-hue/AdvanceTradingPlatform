/* P1.2: every colour is a CSS variable (src/styles/tokens.css, src/styles/palette.css), so the dark / light theme and
   the colour-blind option switch the whole app at runtime. The old names (bg, panel, accent, danger...) stay as
   aliases of the semantic tokens so existing pages follow the theme before they are migrated (P1.3). */
const HUES = ["slate", "emerald", "green", "rose", "red", "amber", "yellow", "orange", "sky", "blue", "cyan", "teal",
  "lime", "indigo", "violet", "purple", "fuchsia", "pink"];
const SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const palette = (prefix) => Object.fromEntries(HUES.map((h) => [h, Object.fromEntries(SHADES.map((s) => [s, v(`${prefix}-${h}-${s}`)]))]));

const semantic = {
  // semantic tokens
  surface: v("surface"), "surface-1": v("surface-1"), "surface-2": v("surface-2"), "surface-3": v("surface-3"),
  fg: v("fg"), "fg-muted": v("fg-muted"), border: v("border"),
  brand: v("brand"), "brand-strong": v("brand-strong"), "on-brand": v("on-brand"),
  up: v("up"), down: v("down"), warn: v("warn"), info: v("info"),
  ai: v("ai"), "ai-2": v("ai-2"), glass: v("glass"),
  // aliases kept for pages not yet migrated
  bg: v("surface"), panel: v("surface-1"), panel2: v("surface-2"), panel3: v("surface-3"),
  "brand-dim": v("brand-strong"), accent: v("up"), danger: v("down"), muted: v("fg-muted"),
};

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}", "./.storybook/**/*.{ts,tsx}"],
  darkMode: ["selector", ':root:not([data-theme="light"])'],
  theme: {
    extend: {
      colors: { ...semantic, ...palette("bgc") },
      textColor: { ...semantic, ...palette("tx") },
      borderColor: { ...semantic, ...palette("tx") },
      ringColor: { ...semantic, ...palette("tx") },
      placeholderColor: { ...semantic, ...palette("tx") },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "var(--shadow)",
        glow: "0 0 0 1px rgb(var(--brand) / 0.4), 0 0 24px rgb(var(--brand) / 0.15)",
      },
    },
  },
  plugins: [],
};

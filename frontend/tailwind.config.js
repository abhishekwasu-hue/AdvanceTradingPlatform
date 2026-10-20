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
  // U5 (screener design): the recessed surface, the funnel trail / matched colour, the armed (auto-execution) colour
  "surface-inset": v("surface-inset"), signal: v("signal"), armed: v("armed"),
  // UI2-0 (UI v2 §8): the subtle outline / divider colour and the warning-note background
  "border-subtle": v("border-subtle"), "warn-tint": v("warn-tint"),
  // UI2-1: the backdrop behind the More sheet and the command bar (with an alpha: `bg-scrim/50`)
  scrim: v("scrim"),
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
      // Filled brand surfaces read the fill tokens (WCAG AA under white text in both themes); text, borders and rings
      // keep --brand. `bg-brand`, `hover:bg-brand-strong` and their alpha tints follow this.
      backgroundColor: { brand: v("brand-fill"), "brand-strong": v("brand-fill-strong"), "brand-dim": v("brand-fill-strong") },
      textColor: { ...semantic, ...palette("tx") },
      // UI2-0: `border-subtle` (card outlines, dividers, table rows) reads --border-subtle.
      borderColor: { ...semantic, ...palette("tx"), subtle: v("border-subtle") },
      ringColor: { ...semantic, ...palette("tx") },
      placeholderColor: { ...semantic, ...palette("tx") },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      // U5: radii (controls 6, panels 10) and the type scale 12 / 13 / 15 / 18 / 24 / 32
      // UI2-0: `rounded-pill` for chips and badges.
      borderRadius: { control: "var(--radius-control)", panel: "var(--radius-panel)", pill: "var(--radius-pill)" },
      fontSize: {
        "t12": ["12px", { lineHeight: "16px" }], "t13": ["13px", { lineHeight: "18px" }], "t15": ["15px", { lineHeight: "20px" }],
        // UI2-0: body text (the de-facto text-sm made a token) and the phone page title
        "t14": ["14px", { lineHeight: "20px" }], "t20": ["20px", { lineHeight: "26px" }],
        "t18": ["18px", { lineHeight: "24px" }], "t24": ["24px", { lineHeight: "30px" }], "t32": ["32px", { lineHeight: "38px" }],
      },
      // UI2-0: the 4-pt spacing tokens, as `p-space-4`, `gap-space-6`, `px-space-8`...
      spacing: Object.fromEntries([1, 2, 3, 4, 5, 6, 8, 10, 12].map((n) => [`space-${n}`, `var(--space-${n})`])),
      transitionDuration: { fast: "var(--dur-fast)", base: "var(--dur-base)" },
      transitionTimingFunction: { standard: "var(--ease)" },
      height: { row: "var(--row-h)", stage: "var(--stage-h)" },
      minHeight: { row: "var(--row-h)", stage: "var(--stage-h)" },
      boxShadow: {
        card: "var(--shadow)",
        raised: "var(--shadow-raised)",   // UI2-0: menus, drawers, sheets, dialogs
        glow: "0 0 0 1px rgb(var(--brand) / 0.4), 0 0 24px rgb(var(--brand) / 0.15)",
      },
    },
  },
  plugins: [],
};

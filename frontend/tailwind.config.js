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
  // Chart + colour order Part 3 (CC-1..CC-4): the range bar (a neutral track, an amber-yellow marker), the info and AI
  // chip backgrounds. `bg-range-track`, `bg-range`, `bg-info-tint text-info`, `bg-ai-tint text-ai`.
  range: v("range"), "range-track": v("range-track"), "info-tint": v("info-tint"), "ai-tint": v("ai-tint"),
  // Colour v2 (owner, 23 Oct; behind `colour_v2`): soft tints by meaning, the option chain's sides and OI bars, the
  // soft icon circles and the Insights group colour. `bg-up-tint text-up`, `bg-soft-ai text-ai`, `bg-chain-call`...
  "brand-tint": v("brand-tint"), "up-tint": v("up-tint"), "down-tint": v("down-tint"), "teal-accent": v("teal-accent"),
  "soft-brand": v("soft-brand"), "soft-ai": v("soft-ai"), "soft-warn": v("soft-warn"), "soft-down": v("soft-down"),
  "soft-up": v("soft-up"), "soft-teal": v("soft-teal"), "soft-info": v("soft-info"),
  "chain-call": v("chain-call"), "chain-call-itm": v("chain-call-itm"), "chain-put": v("chain-put"), "chain-put-itm": v("chain-put-itm"),
  "oi-call": v("oi-call"), "oi-put": v("oi-put"), "nav-insights": v("nav-insights"),
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
      // U5: radii (controls 6, panels 10). UI2-0: `rounded-pill` for chips and badges.
      borderRadius: { control: "var(--radius-control)", panel: "var(--radius-panel)", pill: "var(--radius-pill)" },
      // Handover item 1 (readable text): every size is in rem, so the one root scale (`--text-scale` on <html>, the
      // Appearance "Text size" setting) moves all text, spacing and tap targets together. At the Normal size: body text
      // 16 px (sm / t14), secondary text and table cells never below 14 px (xs / t12 / t13), headings and key numbers
      // larger. The old token names keep their roles (t12 = caption, t14 = body ...) at the new sizes.
      fontSize: {
        xs: ["0.875rem", { lineHeight: "1.25rem" }], sm: ["1rem", { lineHeight: "1.5rem" }],
        base: ["1.0625rem", { lineHeight: "1.625rem" }], lg: ["1.1875rem", { lineHeight: "1.75rem" }],
        xl: ["1.375rem", { lineHeight: "1.875rem" }], "2xl": ["1.625rem", { lineHeight: "2.125rem" }],
        "3xl": ["2rem", { lineHeight: "2.5rem" }], "4xl": ["2.5rem", { lineHeight: "2.875rem" }], "5xl": ["3.25rem", { lineHeight: "1" }],
        "t12": ["0.875rem", { lineHeight: "1.25rem" }], "t13": ["0.875rem", { lineHeight: "1.25rem" }],
        "t14": ["1rem", { lineHeight: "1.5rem" }], "t15": ["1.0625rem", { lineHeight: "1.5rem" }],
        "t18": ["1.25rem", { lineHeight: "1.75rem" }], "t20": ["1.375rem", { lineHeight: "1.875rem" }],
        "t24": ["1.75rem", { lineHeight: "2.25rem" }], "t32": ["2.25rem", { lineHeight: "2.75rem" }],
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

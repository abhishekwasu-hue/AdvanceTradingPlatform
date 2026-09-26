/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#090c12",
        panel: "#10141d",
        panel2: "#171d2b",
        panel3: "#1e2536",
        border: "#242c3f",
        // The interactive/brand color (buttons, links, active nav, focus rings) - deliberately
        // distinct from `accent`, which stays reserved for bullish/positive P&L semantics so a
        // "click me" action is never visually confused with a "this is a buy signal" indicator.
        brand: "#3b82f6",
        "brand-dim": "#1d4ed8",
        accent: "#22c55e",
        danger: "#ef4444",
        warn: "#f59e0b",
        muted: "#8a94a8",
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.02)",
        glow: "0 0 0 1px rgba(59,130,246,0.4), 0 0 24px rgba(59,130,246,0.15)",
      },
    },
  },
  plugins: [],
};

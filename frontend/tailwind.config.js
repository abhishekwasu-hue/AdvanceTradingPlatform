/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: "#0b0f17",
        panel: "#111827",
        panel2: "#1a2333",
        border: "#243044",
        accent: "#22c55e",
        danger: "#ef4444",
        warn: "#f59e0b",
        muted: "#8b96a8",
      },
    },
  },
  plugins: [],
};

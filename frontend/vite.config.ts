import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    // The import graph per entry (dist/.vite/manifest.json): scripts/check-bundle-budget.mjs measures each route from it.
    manifest: true,
    rollupOptions: {
      output: {
        // P1.1: the chart library is its own chunk, fetched only by pages that draw a chart.
        manualChunks: { charts: ["lightweight-charts"] },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});

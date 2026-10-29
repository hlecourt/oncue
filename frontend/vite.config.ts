import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  build: { outDir: "../src/subtitle_guard/api/static", emptyOutDir: true, modulePreload: { polyfill: false }, assetsInlineLimit: 0 },
  test: { include: ["src/**/*.test.ts"] },
  server: { port: 5173, proxy: { "/api": { target: process.env.SUBTITLE_GUARD_API ?? "http://127.0.0.1:8485", changeOrigin: false } } },
});

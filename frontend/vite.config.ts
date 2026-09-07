/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite + React + TypeScript (not Next.js): this app is a single-page
// dashboard against a REST API with no server-side rendering or file-based
// routing need — Next.js's app router/SSR machinery would be pure overhead
// here. Kept deliberately small per DESIGN.md's "avoid artificial
// complexity" principle, restated for the frontend in the Fase 11 brief.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});

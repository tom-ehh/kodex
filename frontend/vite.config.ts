import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built into the Python package so FastAPI serves it; no Node at runtime.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/static/app/",
  build: {
    outDir: "../src/kodex/static/app",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Dev: forward API and preview requests to a running server (KODEX_API overrides).
    proxy: Object.fromEntries(
      ["/api", "/preview", "/static/preview", "/static/vendor"].map((p) => [p, process.env.KODEX_API ?? "http://127.0.0.1:8000"]),
    ),
  },
});

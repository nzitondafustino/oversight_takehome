/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The API has no CORS middleware, so the browser calls it on the same origin under /api:
// Vite proxies in development, and nginx does the same in the container (nginx.conf).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.API_PROXY_TARGET || "http://localhost:8000";
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    server: {
      port: 5173,
      proxy: { "/api": { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
    },
    preview: {
      port: 4173,
      proxy: { "/api": { target, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") } },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            query: ["@tanstack/react-query"],
            charts: ["recharts"],
            ui: ["@radix-ui/react-popover", "@radix-ui/react-tooltip", "@radix-ui/react-separator", "@radix-ui/react-slot", "lucide-react"],
          },
        },
      },
    },
    test: { environment: "jsdom", globals: true, setupFiles: ["./src/test/setup.ts"], css: false },
  };
});

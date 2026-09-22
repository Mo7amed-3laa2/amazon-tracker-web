import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Proxying keeps the dev server same-origin with the API, so the session
    // cookie is sent on every fetch without any CORS or SameSite special-casing.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    // app/main.py mounts web/dist/assets and falls back to web/dist/index.html.
    outDir: "dist",
    emptyOutDir: true,
  },
});

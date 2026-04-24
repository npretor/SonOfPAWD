import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `vite build` dumps into nano/src/ui/static/ so Flask serves it directly.
// `vite dev` proxies /api and /socket.io to the Flask backend so the frontend
// can be iterated on at :5173 without CORS drama.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../static",
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:5000",
      "/socket.io": {
        target: "http://localhost:5000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API calls to the Django backend so the browser talks to a
// single origin (no CORS dance). In prod the nginx container does this.
const backend = process.env.BACKEND_ORIGIN || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/healthz": { target: backend, changeOrigin: true },
    },
  },
});

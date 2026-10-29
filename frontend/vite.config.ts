import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API = process.env.VITE_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: true, proxy: { "/api": { target: API, changeOrigin: true } } },
});

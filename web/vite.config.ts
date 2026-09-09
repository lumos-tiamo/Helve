import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  // The console is served by the FastAPI app under /console in production, so
  // asset URLs have to be relative to that prefix rather than the domain root.
  base: "/console/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    // Dev runs on its own port; everything under /api goes to the backend so
    // the browser sees one origin and CORS never enters the picture.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
});

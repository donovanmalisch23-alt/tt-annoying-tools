import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { webbyBridge } from "./scripts/vite-webby";

// Freebuff injects PORT for isolated workspaces; the dev server must bind
// 0.0.0.0 and HMR stays disabled.
//
// `webbyBridge()` is dev-server only (`apply: "serve"`): it proxies same-origin
// `/api/*` to the Webby bridge and starts that bridge as a child of the dev
// server, so live mode works from the preview without a second terminal. A
// production build is unaffected, and `WEBBY_DISABLE=1` turns it off.
export default defineConfig({
  plugins: [react(), webbyBridge()],
  server: {
    host: true,
    port: Number(process.env.PORT) || 5173,
    strictPort: false,
    hmr: false,
  },
  preview: {
    host: true,
    port: Number(process.env.PORT) || 4173,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});

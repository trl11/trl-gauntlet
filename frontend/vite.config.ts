import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vitest/config";

const root = path.resolve(__dirname, "src");

export default defineConfig({
  root,
  plugins: [react()],
  publicDir: path.resolve(__dirname, "public"),
  resolve: {
    alias: {
      "@api": path.resolve(root, "api"),
      "@assets": path.resolve(root, "assets"),
      "@components": path.resolve(root, "components"),
      "@hooks": path.resolve(root, "hooks"),
      "@pages": path.resolve(root, "pages"),
      "@styles": path.resolve(root, "styles"),
      "@trl11": path.resolve(__dirname, "../extras/trl-ui-kit"),
    },
    // The ui-kit is consumed as source and installs nothing of its own, so
    // its bare imports have to resolve to this app's copies.
    dedupe: [
      "clsx",
      "react",
      "react-dom",
      "@fortawesome/fontawesome-svg-core",
      "@fortawesome/free-solid-svg-icons",
      "@fortawesome/react-fontawesome",
    ],
  },
  esbuild: {
    legalComments: "none",
  },
  server: {
    host: true,
    port: 7101,
    strictPort: true,
    proxy: {
      // The `@api` alias resolves to src/api, so its own modules are served at
      // /api/*.ts -- indistinguishable from a real REST call by prefix alone.
      // Real calls never carry a source-file extension, so bypass those to
      // let Vite serve the module instead of proxying it to the backend.
      "/api": {
        target: "http://127.0.0.1:7100",
        changeOrigin: true,
        ws: true,
        bypass: (req) => (/\.[jt]sx?($|\?)/.test(req.url ?? "") ? req.url : undefined),
      },
      // The API documentation the settings page links to is served by the
      // backend, and without these Vite answers with the SPA shell instead.
      "/docs": { target: "http://127.0.0.1:7100", changeOrigin: true },
      "/openapi.json": { target: "http://127.0.0.1:7100", changeOrigin: true },
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../packages/gauntlet/src/gauntlet/web_dist"),
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [path.resolve(root, "test/setup.ts")],
    include: [path.resolve(root, "**/*.{test,spec}.{ts,tsx}")],
    css: false,
  },
});

import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
export default defineConfig({
  plugins: [tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../fluxyr/static", emptyOutDir: true },
  server: {
    proxy: {
      "/api": process.env.FLUXYR_DEV_BACKEND || "http://127.0.0.1:5050",
      "/preview": process.env.FLUXYR_DEV_BACKEND || "http://127.0.0.1:5050",
    },
  },
});

import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
export default defineConfig({
  plugins: [tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../fluxyr_agent/static", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:5050",
      "/preview": "http://127.0.0.1:5050",
    },
  },
});

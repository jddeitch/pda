// @ts-check
import { defineConfig } from "astro/config";
import node from "@astrojs/node";

// https://astro.build/config
export default defineConfig({
  site: "https://pda.expert",
  output: "static",
  adapter: node({
    mode: "standalone",
  }),
  build: {
    format: "directory",
  },
  vite: {
    css: {
      postcss: "./postcss.config.js",
    },
    define: {
      "import.meta.env.ENABLE_ADMIN": JSON.stringify(
        process.env.ENABLE_ADMIN === "true"
      ),
    },
  },
});

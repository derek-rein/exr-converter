// Astro site for EXR Converter docs.
// Source of truth for pages: ../docs/*.md (loaded in src/content.config.ts).
// Build:  make docs-build   (or: npm run build in site/)
// Serve:  make docs-serve
import { defineConfig } from "astro/config";
import remarkDocLinks from "./src/lib/remark-doc-links.mjs";

export default defineConfig({
  site: "https://exr-converter.derekvfx.ca",
  trailingSlash: "always",
  build: { format: "directory" },
  markdown: {
    smartypants: false,
    remarkPlugins: [remarkDocLinks],
    shikiConfig: { theme: "github-dark" },
  },
  vite: {
    // Keep favicons as real files instead of inlined data: URIs.
    build: { assetsInlineLimit: 0 },
    // Screenshot and favicon come from ../resources (same files as the app).
    server: { fs: { allow: [".."] } },
  },
});

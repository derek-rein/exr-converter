// Rewrite links in docs/*.md so they work on the site:
//   ./cli.md#x            → /docs/cli/#x
//   ../AGENTS.md, README, CHANGELOG, integrations/ → GitHub blob on main
// http(s), mailto and #anchors are left alone.
const GITHUB = "https://github.com/derek-rein/exr-converter";
const REPO_FILES = ["AGENTS.md", "CHANGELOG.md", "README.md", "integrations/"];

export function rewriteDocLink(url) {
  if (/^(https?:|mailto:|#)/.test(url)) return url;
  const [rawPath, frag] = url.split("#", 2);
  const hash = frag === undefined ? "" : `#${frag}`;
  if (rawPath.startsWith("../") || REPO_FILES.some((f) => rawPath.includes(f))) {
    const path = rawPath.replace(/^(\.\.\/|\.\/)+/, "");
    return `${GITHUB}/blob/main/${path}${hash}`;
  }
  if (rawPath.endsWith(".md")) {
    const name = rawPath.replace(/^\.\//, "").replace(/\.md$/, "").replace(/^docs\//, "");
    return `/docs/${name}/${hash}`;
  }
  return url;
}

export default function remarkDocLinks() {
  const walk = (node) => {
    if ((node.type === "link" || node.type === "definition") && node.url) {
      node.url = rewriteDocLink(node.url);
    }
    for (const child of node.children ?? []) walk(child);
  };
  return walk;
}

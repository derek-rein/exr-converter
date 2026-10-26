import changelog from "../../../CHANGELOG.md?raw";

export const SITE_TITLE = "EXR Converter";
export const SITE_DESCRIPTION =
  "Desktop GUI and CLI for video and OpenEXR with OpenColorIO, editing proxies with a watch folder and optional invisible watermark, and signed macOS builds.";
export const GITHUB = "https://github.com/derek-rein/exr-converter";
export const RELEASES = `${GITHUB}/releases`;
export const CHANGELOG_URL = `${GITHUB}/blob/main/CHANGELOG.md`;

// Latest released version: the first "## [X.Y.Z]" heading in CHANGELOG.md
// (the [Unreleased] heading is skipped). vercel.json rebuilds when it changes.
export const LATEST_VERSION = changelog.match(/^## \[(\d+\.\d+\.\d+)\]/m)?.[1] ?? "";
export const LATEST_RELEASE_URL = LATEST_VERSION ? `${RELEASES}/tag/v${LATEST_VERSION}` : `${RELEASES}/latest`;

export const NAV = [
  { name: "Docs", href: "/docs/" },
  { name: "Linux", href: "/docs/linux/" },
  { name: "CLI", href: "/docs/cli/" },
  { name: "GUI", href: "/docs/gui/" },
  { name: "Proxy", href: "/docs/gui/#video--proxy" },
  { name: "ProRes", href: "/docs/prores/" },
  { name: "Nuke", href: "/docs/nuke/" },
  { name: "GitHub", href: GITHUB },
];

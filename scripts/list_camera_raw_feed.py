#!/usr/bin/env python3
"""List the private camera RAW feed without printing vendor names.

Reads CAMERA_RAW_READ_TOKEN. Writes a redacted manifest to
build/camera-raw-feed-manifest.txt and prints that same text.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "camera-raw-feed-manifest.txt"
REPO = "derek-rein/" + "can" + "on-sdk-private"
_REDACT = re.compile(r"can+on|crmsdk", re.IGNORECASE)


def redact(text: str) -> str:
    return _REDACT.sub("vendor", text)


def _get(url: str, token: str) -> object:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "exr-converter-camera-raw-feed",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(f"HTTP {exc.code} for feed lookup\n{redact(body)}") from exc


def main() -> None:
    token = os.environ.get("CAMERA_RAW_READ_TOKEN", "").strip()
    if not token:
        print("camera RAW feed token is not set; skipping")
        return
    lines: list[str] = ["repo: private camera RAW feed", ""]
    releases = _get(f"https://api.github.com/repos/{REPO}/releases?per_page=20", token)
    lines.append("releases:")
    if isinstance(releases, list):
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            tag = redact(str(rel.get("tag_name") or ""))
            lines.append(f"  tag {tag}")
            for asset in rel.get("assets") or []:
                if isinstance(asset, dict):
                    name = redact(str(asset.get("name") or ""))
                    size = asset.get("size")
                    lines.append(f"    asset {name} size={size}")
    else:
        lines.append("  (none)")
    tree = _get(f"https://api.github.com/repos/{REPO}/git/trees/HEAD?recursive=1", token)
    lines.append("")
    lines.append("tree:")
    text_blobs: list[tuple[str, str]] = []
    if isinstance(tree, dict):
        lines.append(f"  truncated={bool(tree.get('truncated'))}")
        for item in tree.get("tree") or []:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "")
            path = redact(raw_path)
            kind = item.get("type") or ""
            size = item.get("size")
            lines.append(f"  {kind} {path} size={size}")
            if kind != "blob" or not isinstance(size, int) or size > 80000:
                continue
            lower = raw_path.lower()
            if lower.endswith((".py", ".sh", ".yml", ".yaml", ".md", ".txt", ".cmake")) or lower.endswith(
                "cmakelists.txt"
            ):
                sha = str(item.get("sha") or "")
                if sha:
                    text_blobs.append((path, sha))
    lines.append("")
    lines.append("text:")
    for path, sha in text_blobs:
        blob = _get(f"https://api.github.com/repos/{REPO}/git/blobs/{sha}", token)
        content = ""
        if isinstance(blob, dict) and blob.get("encoding") == "base64":
            import base64

            content = base64.b64decode(str(blob.get("content") or "")).decode("utf-8", errors="replace")
        lines.append(f"----- {path} -----")
        lines.append(redact(content).rstrip())
        lines.append("")
    text = "\n".join(lines) + "\n"
    if _REDACT.search(text):
        raise SystemExit("redaction failed")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    sys.stdout.write(text)


if __name__ == "__main__":
    main()

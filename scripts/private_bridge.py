"""Locate or download the confidential bridge source kept in private SDK repos.

The R3D / BRAW C ABI bridge sources are NOT part of this MIT repository (SDK
license compliance). They live in the private SDK repos under ``bridge/`` and
are fetched at build time with the same read token used for the SDK release.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def token(*keys: str) -> str:
    for key in (*keys, "GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def fetch_tarball(repo: str, ref: str, tok: str, dest: Path) -> Path:
    """Download ``repo@ref`` tarball and extract only ``bridge/`` into *dest*."""
    url = f"https://api.github.com/repos/{repo}/tarball/{ref}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {tok}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "exr-converter-fetch-bridge",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Failed to fetch bridge source {repo}@{ref}: HTTP {e.code}") from e
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        members = []
        for m in tf.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) == 2 and parts[1].startswith("bridge/"):
                m.name = parts[1]
                members.append(m)
        if not members:
            raise SystemExit(f"No bridge/ directory in {repo}@{ref}")
        tf.extractall(dest, members=members, filter="data")
    return dest / "bridge"


def find_bridge(kind: str, repo: str, ref: str, token_env: str) -> Path:
    """Return the private ``bridge/`` dir for *kind* (``r3d`` / ``braw``)."""
    script = f"build_{kind}_bridge.py"
    env = os.environ.get(f"{kind.upper()}_BRIDGE_SRC", "").strip()
    cands = [Path(env).expanduser()] if env else []
    cands += [
        Path.home() / "code" / f"{kind}-sdk-private" / "bridge",
        ROOT / f".{kind}-bridge" / "bridge",
    ]
    for c in cands:
        if (c / script).is_file():
            return c.resolve()
    tok = token(token_env)
    if not tok:
        raise SystemExit(
            f"{kind} bridge source not found. Set {kind.upper()}_BRIDGE_SRC, clone {repo} "
            f"to ~/code/{kind}-sdk-private, or set {token_env}."
        )
    return fetch_tarball(repo, ref, tok, ROOT / f".{kind}-bridge").resolve()

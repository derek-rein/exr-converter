"""docs/variables.md must list every <token> and alias the app accepts."""

from __future__ import annotations

from pathlib import Path

from src.core.path_tokens import PATH_ALIASES, PATH_TOKEN_GROUPS
from src.core.transcode import _CODEC_SLUGS
from src.render.tokens import _ALIASES, TOKEN_GROUPS

DOC = (Path(__file__).resolve().parents[1] / "docs" / "variables.md").read_text("utf-8")


def _section(title: str) -> str:
    start = DOC.index(f"## {title}")
    nxt = DOC.find("\n## ", start + 1)
    return DOC[start : nxt if nxt != -1 else None]


def test_every_path_token_and_alias_is_documented():
    sec = _section("Output path tokens")
    names = [n for _g, toks in PATH_TOKEN_GROUPS for n, _d, _t in toks]
    missing = [n for n in [*names, *PATH_ALIASES] if f"`<{n}>`" not in sec]
    assert not missing, f"add to docs/variables.md: {missing}"


def test_every_overlay_token_and_alias_is_documented():
    sec = _section("Overlay tokens")
    names = [n for _g, toks in TOKEN_GROUPS for n, _d, _t in toks]
    missing = [n for n in [*names, *_ALIASES] if f"`<{n}>`" not in sec]
    assert not missing, f"add to docs/variables.md: {missing}"


def test_every_codec_slug_is_documented():
    sec = _section("Output path tokens")
    assert not [s for s in set(_CODEC_SLUGS.values()) if f"`{s}`" not in sec]

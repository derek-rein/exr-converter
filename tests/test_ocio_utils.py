"""Unit tests for :mod:`src.core.ocio_utils` colorspace resolution helpers.

These build a real OCIO builtin config (no external files) and skip cleanly
if the runtime OCIO has no usable builtin.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.core import ocio_utils


@pytest.fixture(scope="module")
def config():
    """A small builtin OCIO config, or skip if the runtime can't provide one."""
    try:
        builtins = ocio_utils.list_builtin_configs()
    except Exception:  # pragma: no cover - defensive
        pytest.skip("OCIO builtin registry unavailable")
    if not builtins:
        pytest.skip("no OCIO builtin configs available")

    import PyOpenColorIO as OCIO

    last_err = None
    for name, _label, _rec in builtins:
        try:
            return OCIO.Config.CreateFromBuiltinConfig(name)
        except Exception as e:  # pragma: no cover - try next
            last_err = e
    pytest.skip(f"could not instantiate any builtin config: {last_err}")


class TestWorkingSpace:
    def test_resolves_scene_linear(self, config):
        ws = ocio_utils.get_working_space(config)
        assert isinstance(ws, str) and ws

    def test_overlay_authoring_space_resolves(self, config):
        space = ocio_utils.get_overlay_authoring_space(config)
        assert isinstance(space, str) and space


class TestResolveAlias:
    def test_empty_returns_empty(self, config):
        assert ocio_utils.resolve_alias(config, "") == ""

    def test_unknown_returns_empty(self, config):
        assert ocio_utils.resolve_alias(config, "definitely-not-a-space") == ""

    def test_known_space_roundtrips(self, config):
        ws = ocio_utils.get_working_space(config)
        # Resolving the canonical name should return a valid (non-empty) name.
        assert ocio_utils.resolve_alias(config, ws) != ""


class TestLinearizeOverlay:
    def test_preserves_shape_and_alpha(self, config):
        h, w = 8, 8
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., :3] = 128
        rgba[..., 3] = 200  # alpha channel
        out = ocio_utils.linearize_overlay(config, rgba)
        assert out.shape == (h, w, 4)
        assert out.dtype == np.float32
        # Alpha passes through unchanged (scaled to 0..1), RGB is transformed.
        assert np.allclose(out[..., 3], 200 / 255.0)

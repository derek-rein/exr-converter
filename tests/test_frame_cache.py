"""Unit tests for :class:`src.services.frame_cache.FrameCache` (LRU + byte budget)."""

from __future__ import annotations

import numpy as np
import pytest

from src.services.frame_cache import FrameCache


def _frame(value: int = 0, shape=(4, 4, 3), dtype=np.uint16) -> np.ndarray:
    return np.full(shape, value, dtype=dtype)


@pytest.fixture
def big_cache(qapp) -> FrameCache:
    # Budget large enough to hold many small frames.
    return FrameCache(budget_bytes=10 * 1024 * 1024)


class TestPutGet:
    def test_put_then_get_roundtrips(self, big_cache):
        f = _frame(7)
        big_cache.put(1, f)
        out = big_cache.get(1)
        assert out is not None
        assert np.array_equal(out, f)

    def test_missing_frame_returns_none(self, big_cache):
        assert big_cache.get(99) is None

    def test_contains(self, big_cache):
        big_cache.put(5, _frame())
        assert big_cache.contains(5)
        assert not big_cache.contains(6)

    def test_cached_frames_set(self, big_cache):
        big_cache.put(1, _frame())
        big_cache.put(2, _frame())
        assert big_cache.cached_frames() == {1, 2}

    def test_extra_channels_trimmed_to_three(self, big_cache):
        big_cache.put(1, _frame(shape=(4, 4, 4)))
        out = big_cache.get(1)
        assert out.shape[2] == 3

    def test_invalid_shape_ignored(self, big_cache):
        big_cache.put(1, np.zeros((4, 4), dtype=np.uint16))  # 2D
        assert not big_cache.contains(1)

    def test_reinsert_updates_byte_accounting(self, big_cache):
        big_cache.put(1, _frame(shape=(4, 4, 3)))
        first = big_cache.current_bytes
        big_cache.put(1, _frame(shape=(8, 8, 3)))  # replace, larger
        assert big_cache.current_bytes > first
        assert len(big_cache.cached_frames()) == 1


class TestEviction:
    def test_evicts_lru_under_budget(self, qapp):
        frame_bytes = _frame().nbytes
        cache = FrameCache(budget_bytes=frame_bytes * 2 + 1)  # holds 2
        cache.put(1, _frame())
        cache.put(2, _frame())
        cache.put(3, _frame())  # should evict frame 1 (oldest)
        assert not cache.contains(1)
        assert cache.contains(2)
        assert cache.contains(3)
        assert cache.current_bytes <= cache.budget_bytes

    def test_get_refreshes_lru_recency(self, qapp):
        frame_bytes = _frame().nbytes
        cache = FrameCache(budget_bytes=frame_bytes * 2 + 1)
        cache.put(1, _frame())
        cache.put(2, _frame())
        cache.get(1)  # touch 1 -> now most-recent
        cache.put(3, _frame())  # evicts 2, not 1
        assert cache.contains(1)
        assert not cache.contains(2)

    def test_shrinking_budget_evicts(self, qapp):
        cache = FrameCache(budget_bytes=10 * 1024 * 1024)
        for i in range(5):
            cache.put(i, _frame())
        cache.budget_bytes = _frame().nbytes  # only room for 1
        assert cache.current_bytes <= cache.budget_bytes
        assert len(cache.cached_frames()) == 1

    def test_clear_resets(self, big_cache):
        big_cache.put(1, _frame())
        big_cache.clear()
        assert big_cache.cached_frames() == set()
        assert big_cache.current_bytes == 0


class TestEstimate:
    def test_estimate_zero_when_empty(self, big_cache):
        assert big_cache.estimate_frame_bytes() == 0

    def test_estimate_is_average(self, big_cache):
        f = _frame()
        big_cache.put(1, f)
        big_cache.put(2, f)
        assert big_cache.estimate_frame_bytes() == f.nbytes

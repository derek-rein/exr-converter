"""Unit tests for :mod:`src.services.cache_prefs` (budget parsing + clamping)."""

from __future__ import annotations

from src.services.cache_prefs import (
    _qsettings_int,
    cache_budget_bytes,
    load_cache_budget_pct,
    save_cache_budget_pct,
    total_ram_bytes,
)


class TestQSettingsInt:
    def test_int_value(self, settings):
        settings.setValue("k", 42)
        assert _qsettings_int(settings, "k", 0) == 42

    def test_string_value(self, settings):
        settings.setValue("k", "37")
        assert _qsettings_int(settings, "k", 0) == 37

    def test_garbage_string_falls_back(self, settings):
        settings.setValue("k", "not-a-number")
        assert _qsettings_int(settings, "k", 99) == 99

    def test_missing_uses_default(self, settings):
        assert _qsettings_int(settings, "absent", 5) == 5


class TestBudgetPct:
    def test_default_when_unset(self, settings):
        assert load_cache_budget_pct(settings) == 25

    def test_clamps_high(self, settings):
        save_cache_budget_pct(settings, 200)
        assert load_cache_budget_pct(settings) == 90

    def test_clamps_low(self, settings):
        save_cache_budget_pct(settings, -5)
        assert load_cache_budget_pct(settings) == 1

    def test_roundtrip(self, settings):
        save_cache_budget_pct(settings, 40)
        assert load_cache_budget_pct(settings) == 40


class TestBudgetBytes:
    def test_total_ram_positive(self):
        assert total_ram_bytes() > 0

    def test_budget_is_fraction_of_ram(self, settings):
        save_cache_budget_pct(settings, 25)
        budget = cache_budget_bytes(settings)
        assert 0 < budget < total_ram_bytes()

"""Multiprocessing set-up for packaged (Nuitka) builds."""

from __future__ import annotations

import multiprocessing

from src.core import mp_support
from src.core.mp_support import frozen_resource_tracker_args


class TestResourceTrackerRewrite:
    def test_python_3_13_7_plus_form(self):
        # 3.13.7+ builds this list itself; Nuitka's build-time patch misses it.
        args = [
            "/app/exr_converter",
            "-OO",
            "-c",
            "from multiprocessing.resource_tracker import main;main(7)",
        ]
        assert frozen_resource_tracker_args(args) == [
            "/app/exr_converter",
            "--multiprocessing-resource-tracker",
            "7",
        ]

    def test_older_form_with_spaces(self):
        args = ["exe", "-c", "from multiprocessing.resource_tracker import main; main(12)"]
        assert frozen_resource_tracker_args(args) == [
            "exe",
            "--multiprocessing-resource-tracker",
            "12",
        ]

    def test_leaves_other_commands_alone(self):
        assert frozen_resource_tracker_args(["exe", "--multiprocessing-fork", "x"]) is None
        assert frozen_resource_tracker_args(["exe", "-c", "print(1)"]) is None
        assert frozen_resource_tracker_args(["exe", "-c"]) is None

    def test_patch_wraps_spawn_once(self, monkeypatch):
        from multiprocessing import util

        calls: list[list[str]] = []

        def fake_spawn(path, args, passfds):
            calls.append(list(args))
            return 123

        monkeypatch.setattr(util, "spawnv_passfds", fake_spawn)
        mp_support._patch_resource_tracker_launch()
        mp_support._patch_resource_tracker_launch()  # idempotent
        pid = util.spawnv_passfds(
            "exe", ["exe", "-c", "from multiprocessing.resource_tracker import main;main(3)"], ()
        )
        util.spawnv_passfds("exe", ["exe", "-c", "print(1)"], ())
        assert pid == 123
        assert calls == [
            ["exe", "--multiprocessing-resource-tracker", "3"],
            ["exe", "-c", "print(1)"],
        ]


class TestConfigure:
    def test_source_run_uses_spawn_and_keeps_executable(self, monkeypatch):
        seen: dict[str, object] = {}
        monkeypatch.setattr(mp_support, "is_frozen", lambda: False)
        monkeypatch.setattr(
            multiprocessing, "set_start_method", lambda m, force=False: seen.update(method=m)
        )
        monkeypatch.setattr(multiprocessing, "set_executable", lambda e: seen.update(exe=e))
        mp_support.configure_multiprocessing()
        assert seen == {"method": "spawn"}

    def test_frozen_run_sets_executable_and_patches(self, monkeypatch):
        seen: dict[str, object] = {}
        monkeypatch.setattr(mp_support, "is_frozen", lambda: True)
        monkeypatch.setattr(mp_support.sys, "executable", "/opt/app/exr_converter")
        monkeypatch.setattr(multiprocessing, "set_start_method", lambda m, force=False: None)
        monkeypatch.setattr(multiprocessing, "set_executable", lambda e: seen.update(exe=e))
        monkeypatch.setattr(
            mp_support, "_patch_resource_tracker_launch", lambda: seen.update(patched=True)
        )
        mp_support.configure_multiprocessing()
        assert seen["exe"] == "/opt/app/exr_converter"
        if mp_support.sys.platform != "win32":
            assert seen["patched"] is True

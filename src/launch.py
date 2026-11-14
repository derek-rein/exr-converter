"""Decide how a launch should run, before anything heavy is imported.

``main.py`` imports this first. Starting the GUI, or the viewer for a
double-clicked file, must not pull in the command-line pipeline (PyAV, NumPy,
the watermark detector: about a quarter of a second), so the full parser in
``src/cli.py`` is imported only when the arguments need it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

CLI_COMMANDS = frozenset({"video2exr", "exr2video", "proxy", "transcode"})
# Flags the full parser handles on its own: help, version, and the hidden headless switch.
_CLI_FLAGS = frozenset({"-h", "--help", "-V", "--version", "--headless"})


def needs_cli(argv: list[str]) -> bool:
    """True when *argv* names a convert subcommand or a flag only the full parser handles."""
    return any(a in CLI_COMMANDS or a in _CLI_FLAGS for a in argv)


@dataclass
class GuiArgs:
    """The GUI launch options, without the convert subcommands."""

    open: str | None = None
    gui_ocio: str | None = None
    mode: str = "auto"
    smoke_test: bool = False
    view: bool = False  # open the quick viewer, not the main window
    paths: list[str] = field(default_factory=list)  # files to view
    workers_global: int = 0


def parse_gui_args(argv: list[str]) -> GuiArgs:
    """Parse the GUI-only launch options.

    Bare paths are files to view, which is how Windows (``"%1"``) and Linux
    (``%F``) hand over a double-clicked file. ``--view`` says the same thing
    explicitly and may be repeated.
    """
    parser = argparse.ArgumentParser(prog="exr-converter", add_help=False)
    parser.add_argument("--open", default=None)
    parser.add_argument("--gui-ocio", dest="gui_ocio", default=None)
    parser.add_argument(
        "--mode",
        choices=["auto", "video2exr", "exr2video", "proxy", "transcode"],
        default="auto",
    )
    parser.add_argument("--smoke-test", dest="smoke_test", action="store_true")
    parser.add_argument("--workers", dest="workers_global", type=int, default=0)
    parser.add_argument("--view", action="append", default=[], metavar="PATH")
    parser.add_argument("paths", nargs="*")
    ns, unknown = parser.parse_known_args(argv)
    if unknown:
        # Unknown flags are the full parser's to reject with its usage message.
        raise UnknownOptions(unknown)
    paths = [p for p in [*ns.view, *ns.paths] if p]
    return GuiArgs(
        open=ns.open,
        gui_ocio=ns.gui_ocio,
        mode=ns.mode,
        smoke_test=ns.smoke_test,
        view=bool(paths),
        paths=paths,
        workers_global=ns.workers_global,
    )


class UnknownOptions(Exception):
    """The arguments hold something only the full parser understands."""

    def __init__(self, options: list[str]) -> None:
        super().__init__(" ".join(options))
        self.options = options

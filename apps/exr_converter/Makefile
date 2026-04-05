.PHONY: run lint fmt resources bundle clean

APP_NAME := exr_converter
ENTRY    := main.py
VENV     := .venv
UV       := uv
PYTHON   := $(UV) run python
PYINST   := $(UV) run pyinstaller
RCC      := $(UV) run pyside6-rcc

# ── Run ──────────────────────────────────────────────────────────────────────

run:
	$(PYTHON) $(ENTRY)

# ── Lint & Format ────────────────────────────────────────────────────────────

lint:
	$(UV) run ruff check src/ main.py

fmt:
	$(UV) run ruff format src/ main.py
	$(UV) run ruff check --fix src/ main.py

# ── Qt Resources ─────────────────────────────────────────────────────────────

resources: src/rc_resources.py

src/rc_resources.py: resources.qrc public/icon.png
	$(RCC) resources.qrc -o src/rc_resources.py

# ── Bundle with PyInstaller ──────────────────────────────────────────────────
# Produces  dist/$(APP_NAME)  (single-directory bundle).
# macOS .app bundle:  make bundle EXTRA="--windowed"

HIDDEN_IMPORTS := \
	--hidden-import av \
	--hidden-import numpy \
	--hidden-import OpenImageIO \
	--hidden-import PyOpenColorIO \
	--hidden-import pyseq \
	--hidden-import PySide6 \
	--hidden-import src \
	--hidden-import src.cli \
	--hidden-import src.constants \
	--hidden-import src.convert \
	--hidden-import src.exr_io \
	--hidden-import src.ocio_utils \
	--hidden-import src.sequence \
	--hidden-import src.video \
	--hidden-import src.widgets \
	--hidden-import src.window \
	--hidden-import src.worker \
	--hidden-import src.pool \
	--hidden-import src.presets \
	--hidden-import src.rc_resources

# Collect entire packages so native .so/.dylib files are included
COLLECT := \
	--collect-all av \
	--collect-all OpenImageIO \
	--collect-all PyOpenColorIO \
	--collect-all PySide6

EXTRA ?=

# Platform icon: override with ICON=public/icon.ico on Windows
ICON ?= public/icon.icns

bundle: resources
	$(PYINST) \
		--noconfirm \
		--name $(APP_NAME) \
		--icon $(ICON) \
		$(HIDDEN_IMPORTS) \
		$(COLLECT) \
		$(EXTRA) \
		$(ENTRY)
	@echo ""
	@echo "Bundle ready: dist/$(APP_NAME)"

# macOS .app convenience target
bundle-app:
	$(MAKE) bundle EXTRA="--windowed"

clean:
	rm -rf build dist *.spec __pycache__

.PHONY: run lint fmt resources tailwind bundle clean

APP_NAME := slate_maker
ENTRY    := main.py
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

# ── Tailwind CSS ─────────────────────────────────────────────────────────
# Rebuild compiled CSS from slate.html classes.
# Requires the Tailwind v3 standalone CLI on $PATH or at TAILWIND var.
TAILWIND ?= tailwindcss

tailwind:
	cd templates && $(TAILWIND) -i input.css -o tailwind.css --config tailwind.config.js --minify

# ── Qt Resources ─────────────────────────────────────────────────────────────

resources: src/rc_resources.py

src/rc_resources.py: resources.qrc public/icon.png
	$(RCC) resources.qrc -o src/rc_resources.py

# ── Bundle with PyInstaller ──────────────────────────────────────────────────
# Produces  dist/$(APP_NAME)  (single-directory bundle).
# macOS .app bundle:  make bundle EXTRA="--windowed"

HIDDEN_IMPORTS := \
	--hidden-import numpy \
	--hidden-import OpenImageIO \
	--hidden-import PyOpenColorIO \
	--hidden-import PySide6 \
	--hidden-import src \
	--hidden-import src.constants \
	--hidden-import src.widgets \
	--hidden-import src.worker \
	--hidden-import src.rc_resources

COLLECT := \
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
		--add-data "templates:templates" \
		--add-data "style.qss:." \
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

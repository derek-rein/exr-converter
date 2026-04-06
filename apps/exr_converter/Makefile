.PHONY: run lint fmt resources bundle clean

APP_NAME := exr_converter
ENTRY    := main.py
UV       := uv
PYTHON   := $(UV) run python
RCC      := $(UV) run pyside6-rcc

export NUITKA_ASSUME_YES_FOR_DOWNLOADS := 1

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

src/rc_resources.py: resources.qrc public/icon.png public/style.qss
	$(RCC) resources.qrc -o src/rc_resources.py

# ── Bundle with Nuitka ──────────────────────────────────────────────────────
# macOS: dist/exr_converter.app   Linux: dist/exr_converter   Windows: dist/exr_converter.exe

ICON ?= public/icon.icns

bundle: resources
	$(PYTHON) -m nuitka \
		--standalone \
		--output-dir=dist \
		--output-filename=$(APP_NAME) \
		--assume-yes-for-downloads \
		--python-flag=-OO \
		--enable-plugin=pyside6 \
		--macos-create-app-bundle \
		--macos-app-name="EXR Converter" \
		--macos-app-icon=$(ICON) \
		--nofollow-import-to=tkinter \
		--nofollow-import-to=unittest \
		--nofollow-import-to=pydoc \
		--include-package-data=av \
		--include-package=OpenImageIO \
		--include-package-data=OpenImageIO \
		--include-package=PyOpenColorIO \
		--include-package-data=PyOpenColorIO \
		--include-package=fileseq \
		$(ENTRY)
	mv dist/main.app dist/$(APP_NAME).app

clean:
	rm -rf dist build *.build *.dist *.onefile-build __pycache__

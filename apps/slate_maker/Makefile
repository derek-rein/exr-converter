.PHONY: run lint fmt resources tailwind bundle clean

APP_NAME := slate_maker
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

# ── Bundle with Nuitka ──────────────────────────────────────────────────────
# macOS: dist/slate_maker.app   Linux: dist/slate_maker   Windows: dist/slate_maker.exe

ICON ?= public/icon.icns

bundle: resources
	$(PYTHON) -m nuitka \
		--standalone \
		--output-dir=dist \
		--output-filename=$(APP_NAME) \
		--assume-yes-for-downloads \
		--python-flag=-OO \
		--enable-plugin=pyside6 \
		--enable-plugin=anti-bloat \
		--macos-create-app-bundle \
		--macos-app-name="Slate Maker" \
		--macos-app-icon=$(ICON) \
		--nofollow-import-to=tkinter \
		--nofollow-import-to=unittest \
		--nofollow-import-to=pydoc \
		--include-package=OpenImageIO \
		--include-package-data=OpenImageIO \
		--include-package=PyOpenColorIO \
		--include-package-data=PyOpenColorIO \
		--include-data-dir=templates=templates \
		--include-data-files=style.qss=style.qss \
		--noinclude-dlls='libcrypto*' \
		--noinclude-dlls='libssl*' \
		$(ENTRY)
	mv dist/main.app dist/$(APP_NAME).app

clean:
	rm -rf dist build *.build *.dist *.onefile-build __pycache__

# EXR Converter — build, run, lint, release
#
# Usage:
#   make help
#   make run                              # launch the GUI
#   make bump PART=minor                  # bump semver + sync APP_VERSION + uv lock
#   make release PART=patch               # bump + lock + commit + tag + push (triggers Release workflow)
#   make release PUSH=0                   # … local only; push branch + tag yourself to trigger CI

.PHONY: help run lint fmt resources bundle clean bump release

APP_NAME := exr_converter
MACOS_BUNDLE_NAME := EXR Converter
ENTRY    := main.py
UV       := uv
PYTHON   := $(UV) run python
RCC      := $(UV) run pyside6-rcc
BUMP     := python3 scripts/bump_app_version.py
PART     ?= patch
# PUSH=1 (default): push branch + tag so GitHub receives the tag and runs .github/workflows/release.yml
PUSH     ?= 1

export NUITKA_ASSUME_YES_FOR_DOWNLOADS := 1

# ── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo "EXR Converter"
	@echo ""
	@echo "  make run                               # launch the GUI"
	@echo "  make lint / fmt                        # ruff check / format"
	@echo "  make resources                         # regenerate Qt resources"
	@echo "  make bundle                            # Nuitka standalone build"
	@echo "  make clean                             # remove build artifacts"
	@echo ""
	@echo "  make bump PART=patch|minor|major       # bump version (no git)"
	@echo "  make release PART=patch                # bump + commit + tag + push (Release workflow)"
	@echo "  make release PUSH=0                    # bump + commit + tag only (push manually for CI)"
	@echo ""
	@echo "Current tags: git tag -l 'v*' --sort=-v:refname | head"

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

# ── Bundle with Nuitka ───────────────────────────────────────────────────────
# macOS: dist/"EXR Converter.app"   Linux: dist/exr_converter   Windows: dist/exr_converter.exe

ICON ?= public/icon.icns

bundle: resources
	$(PYTHON) -m nuitka \
		--standalone \
		--output-dir=dist \
		--output-filename=$(APP_NAME) \
		--assume-yes-for-downloads \
		--python-flag=-OO \
		--lto=yes \
		--enable-plugin=pyside6 \
		--macos-create-app-bundle \
		--macos-app-name="EXR Converter" \
		--macos-app-icon=$(ICON) \
		--nofollow-import-to=tkinter \
		--nofollow-import-to=unittest \
		--nofollow-import-to=pydoc \
		--nofollow-import-to=PIL \
		--nofollow-import-to='PySide6.QtWebEngine*' \
		--noinclude-qt-translations \
		--noinclude-qt-plugins=printsupport,mediaservice,iconengines \
		--noinclude-dlls='*Qt6WebEngine*' \
		--noinclude-dlls='*Qt6Svg*' \
		--noinclude-dlls='*Qt6Pdf*' \
		--noinclude-dlls='*Qt6Positioning*' \
		--noinclude-dlls='*Qt6PrintSupport*' \
		--include-package-data=av \
		--include-package=OpenImageIO \
		--include-package-data=OpenImageIO \
		--include-package=PyOpenColorIO \
		--include-package-data=PyOpenColorIO \
		--include-package=fileseq \
		--include-data-files=resources/ocio/=resources/ocio/ \
	--noinclude-dlls='libcrypto*' \
		--noinclude-dlls='libssl*' \
		$(ENTRY)
	mv dist/main.app "dist/$(MACOS_BUNDLE_NAME).app"

clean:
	rm -rf dist build *.build *.dist *.onefile-build __pycache__

# ── Version bump (no git) ────────────────────────────────────────────────────

bump:
	@$(BUMP) bump $(PART)
	@$(UV) lock
	@echo "Done. Review diff, then: make release PART=$(PART)  (or commit manually)"

# ── Release: bump + commit + tag (+ optional push) ──────────────────────────

release:
	@set -e; \
	$(BUMP) bump $(PART); \
	$(UV) lock; \
	eval $$($(BUMP) show); \
	if [ -z "$${TAG}" ]; then echo "ERROR: TAG is empty — bump show failed"; exit 1; fi; \
	git add pyproject.toml src/constants.py uv.lock; \
	if git diff --staged --quiet; then echo "No changes to commit."; exit 1; fi; \
	git commit -m "release: $${VERSION}"; \
	git tag "$${TAG}"; \
	echo "Created commit + tag $${TAG}"; \
	if [ "$(PUSH)" = "1" ]; then \
	  git push origin HEAD; \
	  git push origin "$${TAG}"; \
	  echo "Pushed branch and tag $${TAG} (Release workflow runs on tag push)."; \
	else \
	  echo "PUSH=0: tag is local only. To run Release workflow: git push origin HEAD && git push origin $${TAG}"; \
	fi

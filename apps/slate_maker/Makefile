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
	--hidden-import PySide6.QtCore \
	--hidden-import PySide6.QtGui \
	--hidden-import PySide6.QtWidgets \
	--hidden-import PySide6.QtWebChannel \
	--hidden-import PySide6.QtWebEngineCore \
	--hidden-import PySide6.QtWebEngineWidgets \
	--hidden-import PySide6.QtNetwork \
	--hidden-import src \
	--hidden-import src.constants \
	--hidden-import src.widgets \
	--hidden-import src.worker \
	--hidden-import src.rc_resources

COLLECT := \
	--collect-all OpenImageIO \
	--collect-all PyOpenColorIO \
	--collect-submodules PySide6.QtCore \
	--collect-submodules PySide6.QtGui \
	--collect-submodules PySide6.QtWidgets \
	--collect-submodules PySide6.QtWebChannel \
	--collect-submodules PySide6.QtWebEngineCore \
	--collect-submodules PySide6.QtWebEngineWidgets \
	--collect-submodules PySide6.QtNetwork

# Exclude unused Qt modules to reduce bundle size
EXCLUDES := \
	--exclude-module PySide6.Qt3DAnimation \
	--exclude-module PySide6.Qt3DCore \
	--exclude-module PySide6.Qt3DExtras \
	--exclude-module PySide6.Qt3DInput \
	--exclude-module PySide6.Qt3DLogic \
	--exclude-module PySide6.Qt3DRender \
	--exclude-module PySide6.QtBluetooth \
	--exclude-module PySide6.QtCharts \
	--exclude-module PySide6.QtDataVisualization \
	--exclude-module PySide6.QtDesigner \
	--exclude-module PySide6.QtGraphs \
	--exclude-module PySide6.QtHttpServer \
	--exclude-module PySide6.QtLocation \
	--exclude-module PySide6.QtMultimedia \
	--exclude-module PySide6.QtMultimediaWidgets \
	--exclude-module PySide6.QtNetworkAuth \
	--exclude-module PySide6.QtNfc \
	--exclude-module PySide6.QtPdf \
	--exclude-module PySide6.QtPdfWidgets \
	--exclude-module PySide6.QtPositioning \
	--exclude-module PySide6.QtQuick3D \
	--exclude-module PySide6.QtRemoteObjects \
	--exclude-module PySide6.QtScxml \
	--exclude-module PySide6.QtSensors \
	--exclude-module PySide6.QtSerialBus \
	--exclude-module PySide6.QtSerialPort \
	--exclude-module PySide6.QtSpatialAudio \
	--exclude-module PySide6.QtSql \
	--exclude-module PySide6.QtSvg \
	--exclude-module PySide6.QtSvgWidgets \
	--exclude-module PySide6.QtTest \
	--exclude-module PySide6.QtTextToSpeech \
	--exclude-module PySide6.QtWebSockets \
	--exclude-module PySide6.QtXml \
	--exclude-module tkinter \
	--exclude-module unittest \
	--exclude-module email \
	--exclude-module http \
	--exclude-module xmlrpc \
	--exclude-module pydoc

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
		$(EXCLUDES) \
		$(EXTRA) \
		$(ENTRY)
	@# Remove Qt frameworks not needed at runtime (whitelist approach)
	@QT_LIB="dist/$(APP_NAME)/_internal/PySide6/Qt/lib"; \
	KEEP="QtWebEngineCore QtWebEngineWidgets QtWebChannel QtCore QtGui \
	      QtWidgets QtNetwork QtQml QtQmlModels QtQuick QtDBus QtOpenGL \
	      QtPositioning QtPrintSupport QtWebChannelQuick QtQmlMeta"; \
	if [ -d "$$QT_LIB" ]; then \
		for fw in "$$QT_LIB"/*.framework; do \
			name=$$(basename "$$fw" .framework); \
			keep=false; \
			for k in $$KEEP; do \
				if [ "$$name" = "$$k" ]; then keep=true; break; fi; \
			done; \
			if [ "$$keep" = "false" ]; then rm -rf "$$fw"; fi; \
		done; \
	fi
	@rm -rf dist/$(APP_NAME)/_internal/PySide6/Qt/plugins/networkinformation \
	        dist/$(APP_NAME)/_internal/PySide6/Qt/plugins/platforminputcontexts 2>/dev/null || true
	@find dist/$(APP_NAME)/_internal -maxdepth 1 -type d -name 'Qt*' -exec rm -rf {} + 2>/dev/null || true
	@rm -rf dist/$(APP_NAME)/_internal/PIL 2>/dev/null || true
	@echo ""
	@echo "Bundle ready: dist/$(APP_NAME)"

# macOS .app convenience target
bundle-app:
	$(MAKE) bundle EXTRA="--windowed"

clean:
	rm -rf build dist *.spec __pycache__

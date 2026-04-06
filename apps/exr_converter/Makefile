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

src/rc_resources.py: resources.qrc public/icon.png public/style.qss
	$(RCC) resources.qrc -o src/rc_resources.py

# ── Bundle with PyInstaller ──────────────────────────────────────────────────
# Produces  dist/$(APP_NAME)  (single-directory bundle).
# macOS .app bundle:  make bundle EXTRA="--windowed"

HIDDEN_IMPORTS := \
	--hidden-import av \
	--hidden-import numpy \
	--hidden-import OpenImageIO \
	--hidden-import PyOpenColorIO \
	--hidden-import fileseq \
	--hidden-import PySide6.QtCore \
	--hidden-import PySide6.QtGui \
	--hidden-import PySide6.QtWidgets \
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
	--hidden-import src.style \
	--hidden-import src.rc_resources

# Collect entire packages so native .so/.dylib files are included
COLLECT := \
	--collect-all av \
	--collect-all OpenImageIO \
	--collect-all PyOpenColorIO \
	--collect-submodules PySide6.QtCore \
	--collect-submodules PySide6.QtGui \
	--collect-submodules PySide6.QtWidgets

# Exclude unused Qt modules to reduce bundle size (~100+ MB savings)
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
	--exclude-module PySide6.QtQml \
	--exclude-module PySide6.QtQuick \
	--exclude-module PySide6.QtQuick3D \
	--exclude-module PySide6.QtQuickControls2 \
	--exclude-module PySide6.QtQuickWidgets \
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
	--exclude-module PySide6.QtWebChannel \
	--exclude-module PySide6.QtWebEngineCore \
	--exclude-module PySide6.QtWebEngineQuick \
	--exclude-module PySide6.QtWebEngineWidgets \
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
		$(HIDDEN_IMPORTS) \
		$(COLLECT) \
		$(EXCLUDES) \
		$(EXTRA) \
		$(ENTRY)
	@# Remove leaked Qt frameworks not needed at runtime
	@QT_LIB="dist/$(APP_NAME)/_internal/PySide6/Qt/lib"; \
	if [ -d "$$QT_LIB" ]; then \
		for fw in QtPdf QtQuick QtQml QtQmlModels QtQmlMeta QtQmlWorkerScript \
		           QtOpenGL QtSvg QtVirtualKeyboard QtVirtualKeyboardQml; do \
			rm -rf "$$QT_LIB/$${fw}.framework" "$$QT_LIB/lib$${fw}"* 2>/dev/null || true; \
		done; \
	fi
	@rm -rf dist/$(APP_NAME)/_internal/PySide6/Qt/plugins/networkinformation \
	        dist/$(APP_NAME)/_internal/PySide6/Qt/plugins/tls \
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

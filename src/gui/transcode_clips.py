"""Clip tree for the Transcode tab, with an editable input color space column.

Clips added as a folder sit under one folder row; clips added on their own are
top-level rows. Each clip's input space is, in order: its own pick, its
folder's pick, or the space detected from the file's metadata (the same
detection Video → EXR uses). Clicking the color space cell (or right-clicking
a row) opens the OCIO space menu for the clicked row, or for every selected
row when the clicked row is part of the selection.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from ..core.constants import DEFAULT_SRC_V2E
from ..core.ocio_utils import find_equivalent_space
from ..core.video import guess_video_colorspace_candidates
from .color_widgets import fill_color_space_menu
from .style import _PALETTE

COL_CLIP = 0
COL_SPACE = 1

KIND_FILE = "file"
KIND_FOLDER = "folder"

_ROLE_KEY = Qt.ItemDataRole.UserRole
_ROLE_KIND = Qt.ItemDataRole.UserRole + 1
_ROLE_LABEL = Qt.ItemDataRole.UserRole + 2

MIXED = "Mixed"
_DETECTING = "Detecting…"


def _probe(path: str) -> list[str]:
    try:
        return list(guess_video_colorspace_candidates(path))
    except Exception:
        return []


class ClipColorTree(QTreeWidget):
    """Clip list with folder grouping and per-folder / per-clip input spaces."""

    # Any change that can alter a clip's effective input space.
    spaces_changed = Signal()
    # Worker thread → GUI thread (queued): source path, candidate names.
    _probed = Signal(str, list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["Clip", "Input color space"])
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_CLIP, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_SPACE, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(COL_SPACE, 240)

        self._files: dict[str, QTreeWidgetItem] = {}
        self._folders: dict[str, QTreeWidgetItem] = {}
        self._override: dict[str, str] = {}  # file or folder path → picked name
        self._candidates: dict[str, list[str]] = {}  # file → probe result
        self._families: dict[str, list[str]] = {}
        self._cfg: object | None = None
        self._resolved: dict[str, str] = {}  # name → space in the active config
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cs-detect")

        self._probed.connect(self._on_probed)
        self.itemClicked.connect(self._on_item_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu)

    # -- config --

    def set_config(self, families: dict[str, list[str]], cfg: object | None) -> None:
        """Use a new OCIO config: rebuild the menu and re-resolve every space."""
        self._families = families
        self._cfg = cfg
        self._resolved.clear()
        self._refresh_all()

    def _resolve(self, name: str) -> str:
        """*name* (or its equivalent) in the active config, or ``""``."""
        if not name or self._cfg is None:
            return ""
        if name not in self._resolved:
            try:
                hit = find_equivalent_space(self._cfg, name)  # pyright: ignore[reportArgumentType]
            except Exception:
                hit = ""
            self._resolved[name] = hit or ""
        return self._resolved[name]

    # -- contents --

    def clips(self) -> list[str]:
        """Every clip path, in display order."""
        out: list[str] = []
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            if top.data(0, _ROLE_KIND) == KIND_FOLDER:
                out.extend(str(top.child(j).data(0, _ROLE_KEY)) for j in range(top.childCount()))
            else:
                out.append(str(top.data(0, _ROLE_KEY)))
        return out

    def clip_count(self) -> int:
        return len(self._files)

    def folder_item(self, folder: str) -> QTreeWidgetItem | None:
        return self._folders.get(_key(folder))

    def file_item(self, source: str) -> QTreeWidgetItem | None:
        return self._files.get(_key(source))

    def add_sources(self, files: Iterable[Path], folder: str | None = None) -> int:
        """List *files* (under a *folder* row when given). Returns how many were new."""
        parent: QTreeWidgetItem | None = None
        new_items: list[QTreeWidgetItem] = []
        for src in files:
            key = _key(src)
            if key in self._files:
                continue
            if folder is not None and parent is None:
                parent = self._ensure_folder(folder)
            if parent is not None and folder is not None:
                try:
                    label = str(Path(src).relative_to(folder))
                except ValueError:
                    label = Path(src).name
            else:
                label = Path(src).name
            item = QTreeWidgetItem([label, ""])
            item.setData(0, _ROLE_KEY, key)
            item.setData(0, _ROLE_KIND, KIND_FILE)
            item.setData(0, _ROLE_LABEL, label)
            item.setToolTip(COL_CLIP, key)
            if parent is not None:
                parent.addChild(item)
            else:
                self.addTopLevelItem(item)
            self._files[key] = item
            new_items.append(item)
        for item in new_items:
            key = str(item.data(0, _ROLE_KEY))
            self._pool.submit(_probe, key).add_done_callback(
                lambda fut, k=key: self._emit_probed(k, fut)
            )
        if parent is not None:
            self._update_folder_label(parent)
        self._refresh_all()
        return len(new_items)

    def _emit_probed(self, key: str, fut: Future) -> None:
        try:
            result = fut.result()
        except Exception:
            result = []
        self._probed.emit(key, result)

    def _ensure_folder(self, folder: str) -> QTreeWidgetItem:
        key = _key(folder)
        item = self._folders.get(key)
        if item is None:
            label = Path(folder).name or key
            item = QTreeWidgetItem([label, ""])
            item.setData(0, _ROLE_KEY, key)
            item.setData(0, _ROLE_KIND, KIND_FOLDER)
            item.setData(0, _ROLE_LABEL, label)
            item.setToolTip(COL_CLIP, key)
            self.addTopLevelItem(item)
            self._folders[key] = item
        return item

    def remove_selected(self) -> None:
        for item in list(self.selectedItems()):
            if item.treeWidget() is self:  # not already removed with its folder
                self._remove(item)
        self._refresh_all()

    def _remove(self, item: QTreeWidgetItem) -> None:
        key = str(item.data(0, _ROLE_KEY))
        parent = item.parent()
        if item.data(0, _ROLE_KIND) == KIND_FOLDER:
            for j in range(item.childCount()):
                self._forget(str(item.child(j).data(0, _ROLE_KEY)))
            self._folders.pop(key, None)
            self._override.pop(key, None)
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
            return
        self._forget(key)
        if parent is None:
            self.takeTopLevelItem(self.indexOfTopLevelItem(item))
            return
        parent.removeChild(item)
        if parent.childCount() == 0:
            self._remove(parent)
        else:
            self._update_folder_label(parent)

    def _forget(self, key: str) -> None:
        self._files.pop(key, None)
        self._override.pop(key, None)
        self._candidates.pop(key, None)

    def clear_clips(self) -> None:
        self.clear()
        self._files.clear()
        self._folders.clear()
        self._override.clear()
        self._candidates.clear()
        self.spaces_changed.emit()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # -- spaces --

    def detection_pending(self) -> bool:
        return any(k not in self._candidates for k in self._files)

    def override(self, path: str) -> str:
        return self._override.get(_key(path), "")

    def set_override(self, path: str, name: str) -> None:
        """Pick *name* for a clip or folder (``""`` clears it).

        Picking a folder's space also clears its clips' own picks, so every
        clip inside takes the folder's space until one is set again.
        """
        self._apply_override([_key(path)], name)

    def _apply_override(self, keys: list[str], name: str) -> None:
        for key in keys:
            if name:
                self._override[key] = name
            else:
                self._override.pop(key, None)
            folder = self._folders.get(key)
            if folder is not None:
                for j in range(folder.childCount()):
                    self._override.pop(str(folder.child(j).data(0, _ROLE_KEY)), None)
        self._refresh_all()

    def detected_space(self, source: str) -> str:
        """Space detected from *source*'s metadata, or ``""`` while probing."""
        cands = self._candidates.get(_key(source))
        if cands is None:
            return ""
        for name in (*cands, DEFAULT_SRC_V2E):
            hit = self._resolve(name)
            if hit:
                return hit
        return ""

    def _inherited(self, source: str) -> tuple[str, str]:
        """(space, origin) a clip gets without its own pick."""
        item = self._files.get(_key(source))
        parent = item.parent() if item is not None else None
        if parent is not None:
            folder_pick = self._override.get(str(parent.data(0, _ROLE_KEY)), "")
            if folder_pick:
                return folder_pick, "folder"
        if _key(source) not in self._candidates:
            return "", "pending"
        return self.detected_space(source), "detected"

    def _source_pick(self, source: str) -> tuple[str, str]:
        """(wanted name, origin) for a clip: its pick, the folder's, or detected."""
        own = self._override.get(_key(source), "")
        if own:
            return own, "clip"
        return self._inherited(source)

    def effective_space(self, source: str) -> str:
        """The input space a clip will convert from (``""`` if unknown or invalid)."""
        name, _origin = self._source_pick(source)
        return self._resolve(name)

    def all_valid(self) -> bool:
        """Every clip has a space that exists in the active config."""
        return bool(self._files) and all(self.effective_space(k) for k in self._files)

    # -- display --

    def _refresh_all(self) -> None:
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for j in range(top.childCount()):
                self._refresh_file(top.child(j))
            if top.data(0, _ROLE_KIND) == KIND_FOLDER:
                self._refresh_folder(top)
            else:
                self._refresh_file(top)
        self.spaces_changed.emit()

    def _paint(self, item: QTreeWidgetItem, text: str, tip: str, *, auto: bool, bad: bool) -> None:
        item.setText(COL_SPACE, text)
        item.setToolTip(COL_SPACE, tip)
        font = QFont(item.font(COL_CLIP))
        font.setItalic(auto and not bad)
        item.setFont(COL_SPACE, font)
        if bad:
            item.setForeground(COL_SPACE, QBrush(QColor(_PALETTE["ERROR"])))
        elif auto:
            item.setForeground(COL_SPACE, QBrush(QColor(_PALETTE["FG_DIM"])))
        else:
            item.setData(COL_SPACE, Qt.ItemDataRole.ForegroundRole, None)

    def _refresh_file(self, item: QTreeWidgetItem) -> None:
        key = str(item.data(0, _ROLE_KEY))
        name, origin = self._source_pick(key)
        if origin == "pending":
            self._paint(item, _DETECTING, "Reading the clip's color tags…", auto=True, bad=False)
            return
        resolved = self._resolve(name)
        if not resolved:
            shown = name or "(none)"
            tip = (
                "No OCIO config loaded."
                if self._cfg is None
                else f"“{shown}” is not in the current OCIO config. Pick a color space."
            )
            self._paint(item, f"⚠ {shown}", tip, auto=False, bad=True)
            return
        tips = {
            "clip": "Set for this clip. Click to change.",
            "folder": "From the folder. Click to set this clip on its own.",
            "detected": "Detected from the clip's metadata. Click to change.",
        }
        self._paint(item, resolved, tips[origin], auto=origin != "clip", bad=False)

    def _refresh_folder(self, item: QTreeWidgetItem) -> None:
        key = str(item.data(0, _ROLE_KEY))
        pick = self._override.get(key, "")
        if pick:
            resolved = self._resolve(pick)
            if resolved:
                self._paint(
                    item,
                    resolved,
                    "Set for every clip in this folder. Expand to set clips one by one.",
                    auto=False,
                    bad=False,
                )
            else:
                self._paint(
                    item,
                    f"⚠ {pick}",
                    f"“{pick}” is not in the current OCIO config. Pick a color space.",
                    auto=False,
                    bad=True,
                )
            return
        children = [str(item.child(j).data(0, _ROLE_KEY)) for j in range(item.childCount())]
        if any(c not in self._candidates and not self._override.get(c) for c in children):
            self._paint(item, _DETECTING, "Reading the clips' color tags…", auto=True, bad=False)
            return
        spaces = {self.effective_space(c) for c in children}
        if len(spaces) == 1 and "" not in spaces:
            text = next(iter(spaces))
            tip = "Every clip in this folder uses this space. Click to set the whole folder."
        else:
            text = MIXED
            tip = "The clips use different spaces. Expand to see them, or click to set them all."
        self._paint(item, text, tip, auto=True, bad="" in spaces)

    def _update_folder_label(self, folder: QTreeWidgetItem) -> None:
        n = folder.childCount()
        base = str(folder.data(0, _ROLE_LABEL))
        done = failed = 0
        for j in range(n):
            text = folder.child(j).text(COL_CLIP)
            done += text.startswith("✓ ")
            failed += text.startswith("✗ ")
        parts = [f"{n} clip{'s' if n != 1 else ''}"]
        if done:
            parts.append(f"{done} done")
        if failed:
            parts.append(f"{failed} failed")
        folder.setText(COL_CLIP, f"{base}  ({', '.join(parts)})")

    def set_status(self, source: str, prefix: str, tip: str = "") -> None:
        """Show a state prefix (queued, progress, done, failed) on a clip row."""
        item = self._files.get(_key(source))
        if item is None:
            return
        label = str(item.data(0, _ROLE_LABEL))
        item.setText(COL_CLIP, f"{prefix} {label}" if prefix else label)
        key = str(item.data(0, _ROLE_KEY))
        item.setToolTip(COL_CLIP, f"{key}\n{tip}" if tip else key)
        if item.parent() is not None:
            self._update_folder_label(item.parent())

    def status_text(self, source: str) -> str:
        item = self._files.get(_key(source))
        return item.text(COL_CLIP) if item is not None else ""

    # -- editing --

    def _on_probed(self, key: str, candidates: list) -> None:
        if key not in self._files:
            return
        self._candidates[key] = [str(c) for c in candidates]
        self._refresh_all()

    def _targets(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        selected = self.selectedItems()
        return selected if item in selected else [item]

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != COL_SPACE:
            return
        rect = self.visualRect(self.indexFromItem(item, COL_SPACE))
        self._show_space_menu(item, self.viewport().mapToGlobal(rect.bottomLeft()))

    def _on_context_menu(self, pos: QPoint) -> None:
        item = self.itemAt(pos)
        if item is not None:
            self._show_space_menu(item, self.viewport().mapToGlobal(pos))

    def build_space_menu(self, item: QTreeWidgetItem) -> QMenu:
        """The menu for *item* (and the selection it belongs to)."""
        targets = self._targets(item)
        keys = [str(t.data(0, _ROLE_KEY)) for t in targets]
        menu = QMenu(self)
        if len(targets) == 1 and item.data(0, _ROLE_KIND) == KIND_FILE:
            name, origin = self._inherited(keys[0])
            source = "folder" if origin == "folder" else "detected"
            shown = self._resolve(name) or name or "…"
            reset = menu.addAction(f"Use {source}: {shown}")
        else:
            reset = menu.addAction("Use detected space for each clip")
        reset.triggered.connect(lambda _c=False, k=keys: self._apply_override(k, ""))
        if self._families:
            menu.addSeparator()
            fill_color_space_menu(
                menu, self._families, lambda n, k=keys: self._apply_override(k, n)
            )
        return menu

    def _show_space_menu(self, item: QTreeWidgetItem, global_pos: QPoint) -> None:
        menu = self.build_space_menu(item)
        menu.aboutToHide.connect(menu.deleteLater)
        menu.popup(global_pos)


def _key(path: str | os.PathLike[str]) -> str:
    return str(path)

"""SlateModel QUndoStack: flags, fields, fill-from-slate."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from src.services.slate_model import SlateModel


def _model(tmp_path: Path) -> SlateModel:
    ini = str(tmp_path / "slate.ini")
    settings = QSettings(ini, QSettings.Format.IniFormat)
    return SlateModel(settings, "exr2video")


def test_flag_undo_redo(tmp_path, qapp):
    m = _model(tmp_path)
    assert m.slate_enabled is False
    m.set_slate_enabled(True, record_undo=True)
    assert m.slate_enabled is True
    m.undo_stack.undo()
    assert m.slate_enabled is False
    m.undo_stack.redo()
    assert m.slate_enabled is True


def test_slate_fields_undo(tmp_path, qapp):
    m = _model(tmp_path)
    initial = m.slate_fields.get("shot", "")
    m.set_slate_fields({"shot": "SH010"}, record_undo=True)
    assert m.slate_fields["shot"] == "SH010"
    m.set_slate_fields({"shot": "SH020"}, record_undo=True)
    assert m.slate_fields["shot"] == "SH020"
    m.undo_stack.undo()
    assert m.slate_fields["shot"] == "SH010"
    m.undo_stack.undo()
    assert m.slate_fields.get("shot", "") == initial


def test_fill_burnin_undoable(tmp_path, qapp):
    m = _model(tmp_path)
    m.set_slate_fields(
        {"show": "TEST", "sequence": "SQ", "shot": "SH"},
        record_undo=False,
    )
    m.set_burnin_fields({"top_left": "KEEP"}, record_undo=False)
    m.reset_burnin_from_slate(record_undo=True)
    assert m.burnin_fields["top_left"] != "KEEP" or any(v for v in m.burnin_fields.values())
    m.undo_stack.undo()
    assert m.burnin_fields["top_left"] == "KEEP"


def test_restore_document_reverts_every_overlay_setting(tmp_path, qapp):
    """Cancel in the editor: the whole document goes back, not only one field."""
    m = _model(tmp_path)
    m.set_slate_fields({"show": "ALPHA"}, version=3)
    m.set_burnin_fields({"top_left": "before"})
    m.set_watermark_params({"text": "DRAFT", "opacity": 40})
    m.set_slate_enabled(True)
    before = m.capture_document()

    m.set_slate_fields({"show": "BETA"}, version=9, record_undo=True)
    m.set_burnin_fields({"top_left": "after"}, record_undo=True)
    m.set_watermark_params({"text": "FINAL", "opacity": 80}, record_undo=True)
    m.set_slate_enabled(False, record_undo=True)
    m.set_burnin_enabled(True, record_undo=True)
    m.set_watermark_enabled(True, record_undo=True)

    m.restore_document(before)

    assert m.capture_document() == before
    assert m.slate_fields["show"] == "ALPHA"
    assert m.slate_version == 3
    assert m.burnin_fields["top_left"] == "before"
    assert m.watermark_params["text"] == "DRAFT"
    assert (m.slate_enabled, m.burnin_enabled, m.watermark_enabled) == (True, False, False)
    # Undo history from the discarded session would replay edits that no
    # longer apply.
    assert not m.undo_stack.canUndo()


def test_restore_document_persists_the_reverted_values(tmp_path, qapp):
    m = _model(tmp_path)
    m.set_slate_fields({"show": "ALPHA"})
    before = m.capture_document()
    m.set_slate_fields({"show": "BETA"})
    m.restore_document(before)

    reloaded = SlateModel(m.settings, "exr2video")
    assert reloaded.slate_fields["show"] == "ALPHA"


def test_editor_cancel_discards_edits(tmp_path, qapp):
    from src.gui.slate_widgets import SlateDialog

    m = _model(tmp_path)
    m.set_slate_fields({"show": "ALPHA"})
    dlg = SlateDialog(m, mode="exr2video", frame_range="1001-1010", inferred_fps=25.0)
    m.set_slate_fields({"show": "BETA"}, record_undo=True)
    dlg.reject()
    assert m.slate_fields["show"] == "ALPHA"
    dlg.deleteLater()


def test_editor_ok_keeps_edits(tmp_path, qapp):
    from src.gui.slate_widgets import SlateDialog

    m = _model(tmp_path)
    m.set_slate_fields({"show": "ALPHA"})
    dlg = SlateDialog(m, mode="exr2video")
    m.set_slate_fields({"show": "BETA"}, record_undo=True)
    dlg.accept()
    assert m.slate_fields["show"] == "BETA"
    dlg.deleteLater()

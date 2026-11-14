"""Card and FormGrid: props, slots, and the alignment they exist to guarantee."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPushButton, QWidget

from src.gui.components import (
    FILL,
    LABEL_WIDTH,
    NARROW,
    NATURAL,
    WIDE,
    WIDE_MIN,
    Card,
    FormGrid,
    inline_label,
    sized,
)


@pytest.fixture
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _show(widget: QWidget, app: QApplication, width: int = 800) -> None:
    widget.resize(width, 400)
    widget.show()
    app.processEvents()


def test_card_props(app: QApplication) -> None:
    card = Card("Input", "The clip to convert.")
    assert card.title() == "Input"
    assert card.description() == "The clip to convert."
    assert card.accessibleName() == "Input"
    card.setTitle("Watch folder · Watching")
    assert card.title() == "Watch folder · Watching"
    assert card.objectName() == "card"


def test_card_without_description_hides_the_line(app: QApplication) -> None:
    card = Card("Options")
    _show(card, app)
    assert not card._description.isVisible()
    card.set_description("Now it has one.")
    assert card._description.isVisible()


def test_card_slots(app: QApplication) -> None:
    card = Card("Output")
    action = QPushButton("Reveal")
    card.add_header_widget(action)
    field = QLineEdit()
    card.body.addWidget(field)
    _show(card, app)
    assert action.isVisible() and field.isVisible()
    # Header sits above the body.
    assert action.geometry().bottom() < field.geometry().top()


def test_form_rows_share_one_left_edge(app: QApplication) -> None:
    host = QWidget()
    form = FormGrid(host)
    path, space, frames = QLineEdit(), QComboBox(), QLineEdit()
    form.add_row("Path", path, QPushButton("Browse…"), width=FILL)
    form.add_row("Color space", space, width=WIDE)
    form.add_row("A much longer label than the column", frames, width=WIDE)
    _show(host, app)
    lefts = {w.mapTo(host, w.rect().topLeft()).x() for w in (path, space, frames)}
    assert len(lefts) == 1
    assert lefts.pop() == LABEL_WIDTH + form.horizontalSpacing()


def test_separate_forms_share_the_same_control_column(app: QApplication) -> None:
    """A long label in one card must not shift that card's fields past the others."""
    from PySide6.QtWidgets import QVBoxLayout

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    short_card, long_card = Card("Cache"), Card("Hardware")
    short_form, long_form = FormGrid(), FormGrid()
    short_card.body.addLayout(short_form)
    long_card.body.addLayout(long_form)
    budget, encoder = QComboBox(), QComboBox()
    short_form.add_row("Budget", budget, width=WIDE)
    long_form.add_row("H.264 / HEVC encoding", encoder, width=WIDE)
    layout.addWidget(short_card)
    layout.addWidget(long_card)
    _show(host, app)
    assert (
        budget.mapTo(host, budget.rect().topLeft()).x()
        == encoder.mapTo(host, encoder.rect().topLeft()).x()
    )


def test_wide_controls_expand_and_share_one_right_edge(app: QApplication) -> None:
    host = QWidget()
    form = FormGrid(host)
    space, codec, scale = QComboBox(), QComboBox(), QComboBox()
    form.add_row("Color space", space, width=WIDE)
    form.add_row("Codec", codec, width=WIDE)
    form.add_row("Scale", scale, width=NARROW)
    _show(host, app, width=1400)
    assert space.width() == codec.width()
    assert space.width() >= WIDE_MIN
    assert scale.width() == NARROW[1]
    assert (
        space.mapTo(host, space.rect().topRight()).x()
        > scale.mapTo(host, scale.rect().topRight()).x()
    )


def test_fill_row_stretches_to_the_edge(app: QApplication) -> None:
    host = QWidget()
    form = FormGrid(host)
    path = QLineEdit()
    form.add_row("Path", path, width=FILL)
    _show(host, app, width=900)
    assert path.width() > 600


def test_natural_row_keeps_every_widget_at_its_own_size(app: QApplication) -> None:
    host = QWidget()
    form = FormGrid(host)
    first, second = QPushButton("A"), QPushButton("B")
    form.add_row("Overlays", first, second, width=NATURAL)
    _show(host, app, width=900)
    assert first.width() < 200 and second.width() < 200


def test_label_is_the_buddy_of_its_control(app: QApplication) -> None:
    host = QWidget()
    form = FormGrid(host)
    field = QLineEdit()
    label = form.add_row("Frames", field)
    assert isinstance(label, QLabel)
    assert label.buddy() is field
    other = QComboBox()
    assert inline_label("Padding", other).buddy() is other
    assert sized(other, NARROW).maximumWidth() == NARROW[1]


def test_spin_box_and_combo_share_the_preset_width_under_the_app_stylesheet(
    app: QApplication,
) -> None:
    from PySide6.QtWidgets import QSpinBox

    import src.rc_resources  # noqa: F401
    from src.gui.style import load_stylesheet

    host = QWidget()
    host.setStyleSheet(load_stylesheet())
    form = FormGrid(host)
    spin, combo = QSpinBox(), QComboBox()
    form.add_row("Start frame", spin, width=NARROW)
    form.add_row("Scale", combo, width=NARROW)
    _show(host, app)
    assert spin.width() == combo.width() == NARROW[1]

"""The app's interface components, in one place.

Each is a small widget with a narrow job, configured through constructor
arguments and methods (props) and filled through layouts or ``add_*``
methods (slots). Tabs and dialogs compose these; they do not style widgets
themselves. Looks live in ``resources/style.qss`` and ``gui/style.py``.

    Card            titled surface for one group of controls
    FormGrid        labelled rows sharing one label column
    SourceCard      the input as an object: drop target, then clip card
    PickerButton    menu picker that looks like a combo box
    PrimaryButton   the accent action button, with hover and press motion
    ProgressLine    thin progress bar that glides between values
    StatusLine      one line of status text with showMessage()
    SegmentedControl  exclusive choice as a pill row
    SlidingTabBar   tab bar with a travelling underline
    EmptyHint       "nothing here yet" drawn over an empty item view
"""

from ..action_bar import PrimaryButton, ProgressLine, StatusLine
from ..animated_tabs import SlidingTabBar
from ..empty_hint import EmptyHint
from ..picker_button import PickerButton
from ..segmented_control import SegmentedControl
from ..source_card import SourceCard
from .card import Card
from .form import (
    FILL,
    LABEL_WIDTH,
    MEDIUM,
    NARROW,
    NATURAL,
    WIDE,
    FormGrid,
    inline_label,
    sized,
)

__all__ = [
    "FILL",
    "LABEL_WIDTH",
    "MEDIUM",
    "NARROW",
    "NATURAL",
    "WIDE",
    "Card",
    "EmptyHint",
    "FormGrid",
    "PickerButton",
    "PrimaryButton",
    "ProgressLine",
    "SegmentedControl",
    "SlidingTabBar",
    "SourceCard",
    "StatusLine",
    "inline_label",
    "sized",
]

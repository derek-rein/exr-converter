"""What a mouse drag on the timeline or the image view is doing right now.

Both widgets used to keep a boolean per kind (``_panning``, ``_zooming``,
``_dragging_playhead``), which allowed combinations that mean nothing: press the
middle button, then the left one, and a pan and a playhead drag were both on.
One value makes a drag exactly one thing, and a second button press during it
is ignored.
"""

from __future__ import annotations

from enum import Enum


class Drag(Enum):
    NONE = "none"
    PAN = "pan"
    ZOOM = "zoom"
    PLAYHEAD = "playhead"

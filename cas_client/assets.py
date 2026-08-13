"""Resolves paths to bundled brand assets (cas_client/assets/) -- logo/icon
files sourced from "Templates y Detalles" and checked into the package so
they ship with the app regardless of the caller's working directory."""

import base64
import os

from PySide6.QtGui import QPixmap

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

LOGO_FULL_PNG = os.path.join(
    _ASSETS_DIR, "logo_full.png"
)  # icon + wordmark, transparent bg
LOGO_MARK_PNG = os.path.join(_ASSETS_DIR, "logo_mark.png")  # icon only, transparent bg
APP_ICON_ICO = os.path.join(_ASSETS_DIR, "app_icon.ico")

# LOGO_MARK_PNG's canvas (623x143) has a lot of transparent padding around
# the actual visible chart-and-arrow icon -- fine when the file is used at
# full size, but scaling the *whole padded canvas* down to a small height
# (e.g. a sidebar/login lockup) wastes most of that height's width on
# nothing visible, silently squeezing whatever sits next to it. This is the
# same bounding box used to regenerate app_icon.ico as a proper square icon.
_LOGO_MARK_VISIBLE_BOX = (233, 1, 427, 143)  # (left, top, right, bottom)


def logo_mark_pixmap() -> QPixmap:
    """Tightly-cropped QPixmap of just the visible icon within
    LOGO_MARK_PNG -- use this instead of `QPixmap(LOGO_MARK_PNG)` wherever
    the mark is scaled down next to other content (see module docstring
    above for why); the raw file path is still there for callers that want
    the original padded canvas as-is."""
    pixmap = QPixmap(LOGO_MARK_PNG)
    if pixmap.isNull():
        return pixmap
    left, top, right, bottom = _LOGO_MARK_VISIBLE_BOX
    return pixmap.copy(left, top, right - left, bottom - top)


def logo_full_data_uri() -> str:
    """Base64 data: URI for LOGO_FULL_PNG -- QTextDocument's HTML renderer
    (used for the generated PDF documents) resolves <img src="data:..."> tags
    directly, so this avoids wiring up a QTextDocument.addResource() call."""
    with open(LOGO_FULL_PNG, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"

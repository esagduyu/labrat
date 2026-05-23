"""Terminal image protocol detection (M22).

Detects which image rendering protocol the current terminal supports by
inspecting environment variables.  No terminal DCS query is performed —
env var detection is fast, reliable, and requires no raw terminal I/O.
"""

from __future__ import annotations

import os
from enum import StrEnum


class ImageProtocol(StrEnum):
    kitty = "kitty"
    iterm2 = "iterm2"
    sixel = "sixel"
    none = "none"


def detect_protocol() -> ImageProtocol:
    """Return the best image protocol supported by the current terminal.

    Detection priority:
    1. Kitty graphics protocol  → TERM=xterm-kitty or KITTY_WINDOW_ID
    2. iTerm2 inline images     → ITERM_SESSION_ID or TERM_PROGRAM=WezTerm/iTerm.app
    3. Sixel                    → TERM contains 'sixel' (niche; rarely set)
    4. None                     → fall back to unicode rendering
    """
    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")

    if "kitty" in term or os.environ.get("KITTY_WINDOW_ID"):
        return ImageProtocol.kitty

    if os.environ.get("ITERM_SESSION_ID") or term_program in ("WezTerm", "iTerm.app"):
        return ImageProtocol.iterm2

    if "sixel" in term:
        return ImageProtocol.sixel

    return ImageProtocol.none

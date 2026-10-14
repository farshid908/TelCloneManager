"""Shared progress-bar style and renderer."""

__TCM_FILE_HASH__ = "6960415539"


_BAR_STYLE = {"filled": "▰", "empty": "▱", "width": 10}


def configure_progress_bar(style):
    _BAR_STYLE.update(style)


def build_progress_bar(current, total, width=None):
    width = max(1, int(width or _BAR_STYLE["width"]))
    if total > 0:
        percent = int(100 * current / total)
    else:
        percent = 0
    percent = max(0, min(100, percent))
    filled = int(width * percent / 100 + 0.5)
    empty = width - filled
    bar = _BAR_STYLE["filled"] * filled + _BAR_STYLE["empty"] * empty
    return f"[{bar}] {percent}%"

import os
import sys


def _get_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int = 0) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float = 0.0) -> float:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _get_optional_int(name: str, default=None):
    value = os.environ.get(name)
    if not value:
        return default
    lowered = value.strip().lower()
    if lowered in ("none", "null", ""):
        return None
    try:
        return int(value)
    except ValueError:
        return default


# ── Telegram API ────────────────────────────────────────────────────────────
API_ID:   int = _get_int("API_ID")
API_HASH: str = _get_str("API_HASH")

# ── Admin ───────────────────────────────────────────────────────────────────

# ── Sessions ────────────────────────────────────────────────────────────────
SESSIONS_DIR:      str = _get_str("SESSIONS_DIR", "./sessions")
MAIN_SESSION_NAME: str = _get_str("MAIN_SESSION_NAME", "main_commander")

# ── Bridge ──────────────────────────────────────────────────────────────────
BRIDGE_GROUP:       int | None = _get_optional_int("BRIDGE_GROUP")
BRIDGE_INVITE_LINK: str        = _get_str("BRIDGE_INVITE_LINK")

# ── Timing ──────────────────────────────────────────────────────────────────
BUTTON_CLICK_TIMEOUT: int   = _get_int("BUTTON_CLICK_TIMEOUT", 30)
AUTO_DELETE_DELAY:    float = _get_float("AUTO_DELETE_DELAY", 1.0)

# ── Web Dashboard ──────────────────────────────────────────────────────────
WEB_PASSWORD:   str = _get_str("WEB_PASSWORD")
WEB_SECRET_KEY: str = _get_str("WEB_SECRET_KEY")

# ── Music ───────────────────────────────────────────────────────────────────
MUSIC_DIR: str = _get_str("MUSIC_DIR", "./music")

# ── Admin API (localhost only) ──────────────────────────────────────────────
ADMIN_API_HOST:  str = _get_str("ADMIN_API_HOST", "127.0.0.1")
ADMIN_API_PORT:  int = _get_int("ADMIN_API_PORT", 1600)
ADMIN_API_TOKEN: str = _get_str("ADMIN_API_TOKEN")

# ── Clone Manager Bot (Inline) ─────────────────────────────────────────────
CLONE_MANAGER_BOT_TOKEN:    str = _get_str("CLONE_MANAGER_BOT_TOKEN")
CLONE_MANAGER_BOT_USERNAME: str = _get_str("CLONE_MANAGER_BOT_USERNAME")

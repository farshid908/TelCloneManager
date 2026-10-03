"""
Main menu for Clone Manager bot.
"""

__TCM_FILE_HASH__ = "9475323654"


import os

from ..utils.keyboards import make_inline_keyboard
from ..normal_mode_store import load_active_mode


def _clone_session_count() -> int:
    """Count all Clone*.session files, including disconnected sessions."""
    try:
        from config import SESSIONS_DIR

        return sum(
            1
            for filename in os.listdir(SESSIONS_DIR)
            if filename.lower().endswith(".session")
            and os.path.splitext(filename)[0].lower().startswith("clone")
        )
    except (FileNotFoundError, OSError):
        return 0


def build_main_menu(automation, user_id=None):
    """Build main menu text and keyboard."""

    total_clones = _clone_session_count()
    online_clones = sum(
        1
        for client in getattr(automation, "clone_clients", [])
        if client.is_connected()
    )
    text = (
        "🤖 Clone Manager — Main Menu\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 Status Overview:\n"
        f"  • Clones: {online_clones}/{total_clones} online\n"
        "  • Mirror: OFF ❌\n"
        "  • Active Loops: __\n\n"
        f"  • Active Mod: {load_active_mode().title()}\n\n"
        "Select a section below:"
    )

    try:
        from commander_manager import active_name, is_primary
        delegated = active_name() != "main_commander"
        primary_user = user_id is not None and is_primary(user_id)
    except Exception:
        delegated = False
        primary_user = False

    if delegated and primary_user:
        return (
            text,
            make_inline_keyboard([[
                ("change commander👁‍🗨", "session:commanders"),
            ]]),
        )

    buttons = [
        [
            ("Clone List 📋", "menu:clone_list"),
            ("Status 📊", "menu:status"),
        ],
        [
            ("Clone Mod 👥", "menu:clone_mode"),
            ("Normal Mod 👤", "menu:normal_main"),
        ],
        [("🔧Session Settings⚙", "menu:session_settings")],
    ]

    try:
        from updater import is_updater_running
        if is_updater_running():
            buttons.append([("Check for update🔁", "update:check")])
    except Exception:
        pass

    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

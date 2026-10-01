"""
Safety menu — FloodWait history, session health, dead sessions.
"""

import os
import glob

from ..utils.keyboards import make_inline_keyboard


def build_config_menu(automation):
    """Build the general configuration menu."""
    bridge = getattr(automation, "bridge_group_id", None)
    clone_mode = getattr(automation, "profile_mode", None) or "unknown"
    text = (
        "⚙️ **Configuration**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🌉 **Bridge Group:** `{bridge if bridge is not None else 'not set'}`\n"
        f"🎨 **Profile Mode:** `{clone_mode}`\n"
        f"👥 **Clones:** `{len(automation.clone_clients)}`\n\n"
        "Choose a configuration section:"
    )
    buttons = [
        [("🔄 Refresh", "menu:config"), ("🏠 Main Menu", "menu:main")],
    ]
    return text, make_inline_keyboard(buttons)


def build_safety_menu(automation):
    """Build safety and health monitoring menu."""

    total = len(automation.clone_clients)
    online = sum(1 for c in automation.clone_clients if c.is_connected())
    offline = total - online

    
    from config import SESSIONS_DIR
    dead_dir = os.path.join(SESSIONS_DIR, "_dead")
    dead_count = 0
    if os.path.isdir(dead_dir):
        dead_count = len(glob.glob(os.path.join(dead_dir, "*.session")))

    
    disconnected = []
    for i, (client, name) in enumerate(
        zip(automation.clone_clients, automation.clone_names)
    ):
        try:
            if not client.is_connected():
                disconnected.append(f"#{i+1} {name}")
        except Exception:
            disconnected.append(f"#{i+1} {name}")

    text = (
        "🛡️ **Safety & Health**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**📊 Session Health:**\n"
        f"  🟢 Online: {online}\n"
        f"  🔴 Offline: {offline}\n"
        f"  💀 Dead (quarantined): {dead_count}\n\n"
    )

    if disconnected:
        text += "**⚠️ Disconnected Clones:**\n"
        for d in disconnected[:10]:
            text += f"  🔴 {d}\n"
        if len(disconnected) > 10:
            text += f"  ... and {len(disconnected) - 10} more\n"
        text += "\n"

    if dead_count > 0:
        text += (
            f"**💀 Dead Sessions:**\n"
            f"  {dead_count} session(s) in `_dead/` folder\n"
            f"  These were auto-quarantined due to:\n"
            f"  AuthKey errors, bans, or revoked sessions\n\n"
        )

    
    main_ok = False
    if automation.main_client:
        try:
            main_ok = automation.main_client.is_connected()
        except Exception:
            pass

    text += (
        f"**🔑 Main Account:**\n"
        f"  {'🟢 Connected' if main_ok else '🔴 Disconnected'}\n\n"
    )

    
    text += (
        f"**🤖 Bot Status:**\n"
        f"  Running: {'✅' if automation.bot_running else '❌'}\n"
    )

    if hasattr(automation, 'startup_error') and automation.startup_error:
        error_short = automation.startup_error[:100]
        text += f"  Last error: `{error_short}`\n"

    buttons = [
        
        [
            ("🔍 Check All Sessions", "action:safety:check_all"),
        ],
        [
            ("🔄 Reconnect Offline", "action:safety:reconnect"),
        ],
        [
            ("💀 View Dead Sessions", "action:safety:dead_list"),
        ],

        
        [
            ("🔄 Refresh", "menu:safety"),
            ("🏠 Main Menu", "menu:main"),
        ],
    ]

    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

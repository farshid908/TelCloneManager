"""
Clone detail menu — shows info and actions for a single clone.
"""

import asyncio
from ..utils.keyboards import make_inline_keyboard


def _get_clone_full_info(automation, clone_idx: int) -> dict:
    """Get detailed info about a clone."""
    zero_idx = clone_idx - 1

    if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
        return None

    client = automation.clone_clients[zero_idx]
    name = automation.clone_names[zero_idx]

    info = {
        "index": clone_idx,
        "name": name,
        "online": False,
        "username": None,
        "first_name": None,
        "last_name": None,
        "phone": None,
        "user_id": None,
        "bio": None,
        "device_model": None,
    }

    try:
        info["online"] = client.is_connected()
    except Exception:
        pass

    try:
        if hasattr(client, "_self_user") and client._self_user:
            me = client._self_user
            info["username"] = me.username
            info["first_name"] = me.first_name
            info["last_name"] = me.last_name
            info["phone"] = me.phone
            info["user_id"] = me.id
    except Exception:
        pass

    # Device model from session
    try:
        session = getattr(client, "session", None)
        if session:
            dc_id = getattr(session, "dc_id", None)
            if dc_id:
                info["dc_id"] = dc_id
    except Exception:
        pass

    return info


def build_clone_detail(automation, clone_idx: int):
    """Build detail view for a specific clone."""

    info = _get_clone_full_info(automation, clone_idx)

    if info is None:
        text = f"⚠️ Clone #{clone_idx} not found."
        buttons = [[("🏠 Main Menu", "menu:main")]]
        return text, make_inline_keyboard(buttons)

    status = "🟢 Online" if info["online"] else "🔴 Offline"
    username = f"@{info['username']}" if info["username"] else "—"
    first = info["first_name"] or "—"
    last = info["last_name"] or "—"
    phone = info["phone"] or "—"
    uid = info["user_id"] or "—"

    text = (
        f"👤 **Clone #{clone_idx} Detail**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**Status:** {status}\n"
        f"**Session:** `{info['name']}`\n"
        f"**ID:** `{uid}`\n\n"
        f"**First Name:** {first}\n"
        f"**Last Name:** {last}\n"
        f"**Username:** {username}\n"
        f"**Phone:** `{phone}`\n"
    )

    buttons = [
        # Profile actions
        [
            ("📸 Profile", f"menu:profile:{clone_idx}"),
            ("🔒 Privacy", f"menu:privacy:{clone_idx}"),
        ],
        # Quick actions
        [
            ("✏️ Name", f"action:profile:{clone_idx}:name"),
            ("📝 Bio", f"action:profile:{clone_idx}:bio"),
        ],
        [
            ("👤 Username", f"action:profile:{clone_idx}:username"),
            ("🧩 Normal Mode", f"menu:normal:{clone_idx}"),
        ],
        # Navigation
        [
            ("◀️ Clone List", "menu:clone_list"),
            ("🏠 Main Menu", "menu:main"),
        ],
    ]

    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

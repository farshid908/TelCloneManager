"""
Profile management menu — photo, name, bio, username for clones.
"""

from ..utils.keyboards import make_inline_keyboard

CLONES_PER_PAGE = 8


def build_profile_main_menu(automation):
    """Build the main profile management menu (clone selector)."""

    total = len(automation.clone_clients)
    online = sum(1 for c in automation.clone_clients if c.is_connected())

    if total == 0:
        text = (
            "📸 **Profile Management**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No clones available."
        )
        buttons = [[("🏠 Main Menu", "menu:main")]]
        return text, make_inline_keyboard(buttons)

    text = (
        "📸 **Profile Management**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Total: **{total}** | Online: **{online}**\n\n"
        "Select a clone to manage its profile,\n"
        "or use **Bulk Actions** for all at once.\n"
    )

    # Clone selector buttons (up to first page)
    clone_buttons = []
    row = []
    limit = min(total, CLONES_PER_PAGE * 2)  # show more in profile view

    for i in range(limit):
        clone_idx = i + 1
        client = automation.clone_clients[i]
        name = automation.clone_names[i]

        try:
            online_status = client.is_connected()
        except Exception:
            online_status = False

        icon = "🟢" if online_status else "🔴"
        short_name = name[:8] if len(name) > 8 else name
        label = f"{icon} {short_name}"

        row.append((label, f"menu:profile:{clone_idx}"))

        if len(row) == 4:
            clone_buttons.append(row)
            row = []

    if row:
        clone_buttons.append(row)

    if total > limit:
        clone_buttons.append([
            ("📋 Full Clone List", "menu:clone_list"),
        ])

    # Bulk profile actions
    clone_buttons.append([
        ("📸 Bulk Photo", "action:bulk:photo"),
        ("✏️ Bulk Name", "action:bulk:name"),
    ])
    clone_buttons.append([
        ("📝 Bulk Bio", "action:bulk:bio"),
        ("🧩 Normal Mode", "menu:normal_main"),
    ])

    # Navigation
    clone_buttons.append([
        ("🏠 Main Menu", "menu:main"),
    ])

    keyboard = make_inline_keyboard(clone_buttons)
    return text, keyboard


def build_profile_clone_menu(automation, clone_idx: int):
    """Build profile menu for a specific clone."""

    zero_idx = clone_idx - 1

    if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
        text = f"⚠️ Clone #{clone_idx} not found."
        buttons = [[("🏠 Main Menu", "menu:main")]]
        return text, make_inline_keyboard(buttons)

    client = automation.clone_clients[zero_idx]
    name = automation.clone_names[zero_idx]

    # Get current info
    first_name = "—"
    last_name = "—"
    username = "—"
    phone = "—"
    has_photo = False
    online = False

    try:
        online = client.is_connected()
    except Exception:
        pass

    try:
        if hasattr(client, "_self_user") and client._self_user:
            me = client._self_user
            first_name = me.first_name or "—"
            last_name = me.last_name or "—"
            username = f"@{me.username}" if me.username else "—"
            phone = me.phone or "—"
            has_photo = bool(me.photo)
    except Exception:
        pass

    status = "🟢 Online" if online else "🔴 Offline"
    photo_status = "✅ Has photo" if has_photo else "❌ No photo"

    text = (
        f"📸 **Profile — Clone #{clone_idx}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**Status:** {status}\n"
        f"**Session:** `{name}`\n\n"
        f"👤 **First Name:** {first_name}\n"
        f"👤 **Last Name:** {last_name}\n"
        f"🔗 **Username:** {username}\n"
        f"📱 **Phone:** `{phone}`\n"
        f"📸 **Photo:** {photo_status}\n"
    )

    buttons = [
        # Name management
        [
            ("✏️ First Name", f"action:profile:{clone_idx}:first_name"),
            ("✏️ Last Name", f"action:profile:{clone_idx}:last_name"),
        ],
        # Bio & Username
        [
            ("📝 Bio", f"action:profile:{clone_idx}:bio"),
            ("🔗 Username", f"action:profile:{clone_idx}:username"),
        ],
        # Photo management
        [
            ("📸 Set Photo", f"action:profile:{clone_idx}:photo"),
            ("🗑️ Remove Photo", f"action:profile:{clone_idx}:remove_photo"),
            ("🧩 Normal Mode", f"menu:normal:{clone_idx}"),
        ],
        # Remove username
        [
            ("🗑️ Remove Username", f"action:profile:{clone_idx}:remove_username"),
        ],
        # Navigation
        [
            ("◀️ Profile Menu", "menu:profile_main"),
            ("👤 Clone Detail", f"menu:clone:{clone_idx}"),
        ],
        [
            ("🏠 Main Menu", "menu:main"),
        ],
    ]

    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

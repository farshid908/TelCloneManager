"""
Privacy settings menu — profile photo, phone, last seen, etc.
"""

__TCM_FILE_HASH__ = "9710334169"


from ..utils.keyboards import make_inline_keyboard

CLONES_PER_PAGE = 8

PRIVACY_KEYS = [
    {"key": "photo",    "label": "📸 Profile Photo", "emoji": "📸"},
    {"key": "phone",    "label": "📱 Phone Number",  "emoji": "📱"},
    {"key": "lastseen", "label": "🕐 Last Seen",     "emoji": "🕐"},
    {"key": "bio",      "label": "📝 Bio",           "emoji": "📝"},
    {"key": "forward",  "label": "↗️ Forwards",      "emoji": "↗️"},
    {"key": "call",     "label": "📞 Calls",         "emoji": "📞"},
    {"key": "invite",   "label": "🔗 Group Invite",  "emoji": "🔗"},
    {"key": "savedmusic", "label": "🎵 Saved Music", "emoji": "🎵"},
]

PRIVACY_VALUES = [
    {"value": "everyone", "label": "🌐 Everyone"},
    {"value": "contacts", "label": "👥 Contacts"},
    {"value": "nobody",   "label": "🔒 Nobody"},
]


def build_privacy_main_menu(automation):
    """Build main privacy menu (clone selector)."""

    total = len(automation.clone_clients)
    online = sum(1 for c in automation.clone_clients if c.is_connected())

    if total == 0:
        text = (
            "🔒 **Privacy Settings**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No clones available."
        )
        buttons = [[("🏠 Main Menu", "menu:main")]]
        return text, make_inline_keyboard(buttons)

    text = (
        "🔒 **Privacy Settings**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Total: **{total}** | Online: **{online}**\n\n"
        "Select a clone to manage its privacy,\n"
        "or use **Bulk** to set for all clones.\n"
    )

    
    clone_buttons = []
    row = []
    limit = min(total, CLONES_PER_PAGE * 2)

    for i in range(limit):
        clone_idx = i + 1
        client = automation.clone_clients[i]
        name = automation.clone_names[i]

        try:
            is_online = client.is_connected()
        except Exception:
            is_online = False

        icon = "🟢" if is_online else "🔴"
        short_name = name[:8] if len(name) > 8 else name
        label = f"{icon} {short_name}"

        row.append((label, f"menu:privacy:{clone_idx}"))

        if len(row) == 4:
            clone_buttons.append(row)
            row = []

    if row:
        clone_buttons.append(row)

    if total > limit:
        clone_buttons.append([
            ("📋 Full Clone List", "menu:clone_list"),
        ])

    
    clone_buttons.append([
        ("🔒 Bulk: Hide All", "action:bulk:privacy:hideall"),
        ("🌐 Bulk: Show All", "action:bulk:privacy:showall"),
    ])

    
    clone_buttons.append([
        ("🏠 Main Menu", "menu:main"),
    ])

    clone_buttons.insert(
        -1,
        [
            (
                "Show profile photo from ID/link",
                "action:bulk:privacy_target:allow",
            ),
            (
                "Hide profile photo from ID/link",
                "action:bulk:privacy_target:deny",
            ),
        ],
    )
    keyboard = make_inline_keyboard(clone_buttons)
    return text, keyboard


def build_privacy_clone_menu(automation, clone_idx: int):
    """Build privacy menu for a specific clone."""

    zero_idx = clone_idx - 1

    if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
        text = f"⚠️ Clone #{clone_idx} not found."
        buttons = [[("🏠 Main Menu", "menu:main")]]
        return text, make_inline_keyboard(buttons)

    name = automation.clone_names[zero_idx]

    text = (
        f"🔒 **Privacy — Clone #{clone_idx}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**Session:** `{name}`\n\n"
        "Select a privacy setting to change:\n\n"
        "For each setting, choose who can see it:\n"
        "  🌐 Everyone | 👥 Contacts | 🔒 Nobody\n"
    )

    buttons = []

    for pk in PRIVACY_KEYS:
        key = pk["key"]
        label = pk["label"]

        
        buttons.append([
            (f"{label}", "noop"),
        ])
        buttons.append([
            ("🌐", f"action:privacy:{clone_idx}:{key}:everyone"),
            ("👥", f"action:privacy:{clone_idx}:{key}:contacts"),
            ("🔒", f"action:privacy:{clone_idx}:{key}:nobody"),
        ])

    
    buttons.append([
        ("🔒 All → Nobody", f"action:privacy:{clone_idx}:all:nobody"),
        ("🌐 All → Everyone", f"action:privacy:{clone_idx}:all:everyone"),
    ])

    
    buttons.append([
        ("◀️ Privacy Menu", "menu:privacy_main"),
        ("👤 Clone Detail", f"menu:clone:{clone_idx}"),
    ])
    buttons.append([
        ("🏠 Main Menu", "menu:main"),
    ])

    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

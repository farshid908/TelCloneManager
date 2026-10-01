"""
Bulk actions menu — apply settings to all clones at once.
"""

from ..utils.keyboards import make_inline_keyboard


def build_bulk_menu(automation):
    """Build bulk actions menu."""
    from ..normal_mode_store import load_active_mode

    total = len(automation.clone_clients)
    online = sum(1 for c in automation.clone_clients if c.is_connected())

    if total == 0:
        text = (
            "🧩 **Clone Mod**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "❌ No clones available."
        )
        buttons = [[("🏠 Main Menu", "menu:main")]]
        return text, make_inline_keyboard(buttons)

    text = (
        "🧩 **Clone Mod**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Apply to all **{total}** clones ({online} online)\n\n"
        "⚠️ These actions affect **ALL** clones.\n"
        "Make sure you know what you're doing!\n"
    )

    active_mode = load_active_mode()
    buttons = [
        [
            (
                f"🧩 Clone Mod: {'ON ✅' if active_mode == 'clone' else 'OFF ❌'}",
                "action:clone:toggle",
            ),
        ],
        
        [("━━ 📸 Profile ━━", "noop")],
        [
            ("📸 Set Photo", "action:bulk:photo"),
            ("🗑️ Remove Photos", "action:bulk:remove_photo"),
        ],
        [
            ("📸 Photo + Watermark", "action:bulk:photo_wm"),
        ],

        
        [("━━ ✏️ Names ━━", "noop")],
        [
            ("✏️ Same Name All", "action:bulk:name"),
            ("✏️ Template Name", "action:bulk:name_template"),
        ],

        
        [("━━ 📝 Bio ━━", "noop")],
        [
            ("📝 Same Bio All", "action:bulk:bio"),
            ("🗑️ Clear All Bios", "action:bulk:clear_bio"),
        ],

        
        [("━━ 🔒 Privacy ━━", "noop")],
        [
            ("🔒 Hide All From Everyone", "action:bulk:privacy:hideall"),
            ("🌐 Show All To Everyone", "action:bulk:privacy:showall"),
        ],
        [
            ("👥 Contacts Only", "action:bulk:privacy:contacts"),
        ],

        
        [("━━ 👥 Groups ━━", "noop")],
        [
            ("🚀 Join Group", "action:group:join"),
            ("👋 Leave Group", "action:group:leave"),
        ],

        
        [("━━ 🔄 Loops ━━", "noop")],
        [
            ("🛑 Stop All Loops", "action:loop:stopall"),
            ("🪞 Mirror Toggle", "action:loop:mirror_toggle"),
        ],

        
        [
            ("🏠 Main Menu", "menu:main"),
        ],
    ]

    buttons.insert(
        6,
        [("Name + Clone Number", "action:bulk:name_number")],
    )
    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

"""Status menu — active loops and mirror state."""

from ..utils.keyboards import make_inline_keyboard


async def build_status_text(automation) -> str:
    """Build status text with loop chat names resolved through Telegram."""
    active_loops = list(getattr(automation.loops, "active", {}).keys())
    mirror = "ON ✅" if automation.mirror_mode else "OFF ❌"

    lines = [
        "📊 **Status**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🔄 **Loop activated:** {len(active_loops)}",
        f"🪞 **Mirror:** {mirror}",
    ]

    if not active_loops:
        lines.append("\nNo active loops.")
        return "\n".join(lines)

    lines.append("\n**Active Loops:**")
    main_client = getattr(automation, "main_client", None)
    for chat_id, text in active_loops:
        chat_name = str(chat_id)
        if main_client is not None:
            try:
                entity = await main_client.get_entity(chat_id)
                chat_name = (
                    getattr(entity, "title", None)
                    or getattr(entity, "first_name", None)
                    or getattr(entity, "username", None)
                    or str(chat_id)
                )
            except Exception:
                pass
        lines.append(f'"{text}" | {chat_id} | {chat_name}')

    return "\n".join(lines)


async def build_status_menu(automation):
    """Build status menu with refresh and main-menu navigation."""
    text = await build_status_text(automation)
    keyboard = make_inline_keyboard([
        [("Refresh🌀", "status:refresh"), ("Main Menu〽️", "menu:main")],
    ])
    return text, keyboard

"""
Loop actions — stop all loops, toggle mirror.
"""

import logging

from ..utils.keyboards import make_inline_keyboard

logger = logging.getLogger("CloneManager")


async def handle_loop_action(event, data: str, automation):
    """
    Handle loop-related actions.

    Data formats:
        action:loop:stopall         → stop all loops
        action:loop:mirror_toggle   → toggle mirror mode
    """
    parts = data.split(":")

    if len(parts) < 3:
        await event.answer("⚠️ Invalid action", alert=True)
        return

    action_type = parts[2]

    if action_type == "stopall":
        await _stop_all_loops(event, automation)
        return

    if action_type == "mirror_toggle":
        await _toggle_mirror(event, automation)
        return

    await event.answer(f"❓ Unknown loop action: {action_type}", alert=True)


async def _stop_all_loops(event, automation):
    """Stop all active loops."""
    if not automation.loops.active:
        await event.answer("ℹ️ No active loops to stop", alert=True)
        return

    count = len(automation.loops.active)

    
    all_keys = list(automation.loops.active.keys())

    
    cancelled = await automation.loops.cancel_all()

    
    try:
        from state_persistence import unregister_loop
        for chat_id, text in all_keys:
            try:
                unregister_loop(chat_id, text)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"[CLONE-MGR] State cleanup failed: {e}")

    logger.info(f"[CLONE-MGR] ✓ Stopped {cancelled} loop(s)")

    
    from ..menus.bulk_actions import build_bulk_menu
    text, keyboard = build_bulk_menu(automation)

    text += f"\n\n🛑 **Stopped {cancelled} loop(s)**"

    await event.edit(text, buttons=keyboard)
    await event.answer(f"🛑 Stopped {cancelled} loop(s)")


async def _toggle_mirror(event, automation):
    """Toggle mirror mode on/off."""
    automation.mirror_mode = not automation.mirror_mode
    new_state = automation.mirror_mode

    state_str = "ON ✅" if new_state else "OFF ❌"

    logger.info(f"[CLONE-MGR] Mirror mode → {state_str}")

    
    from ..menus.bulk_actions import build_bulk_menu
    text, keyboard = build_bulk_menu(automation)

    text += f"\n\n🪞 **Mirror mode: {state_str}**"

    await event.edit(text, buttons=keyboard)
    await event.answer(f"🪞 Mirror: {state_str}")

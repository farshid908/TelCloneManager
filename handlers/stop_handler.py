"""
Stop command handler.
Stops specific loops or all loops.
Also removes from state persistence so they don't resume after restart.
"""

__TCM_FILE_HASH__ = "handlers__stop_handler00003"


import logging

from telethon import events

from command_parser import parse_stop_command
from loop_manager import make_loop_key
from state_persistence import (
    unregister_loop,
    unregister_bounded_sends_for_text,
    unregister_all_bounded_sends,
)

logger = logging.getLogger("TG-Auto")


def register_stop_handler(automation):
    """Register stop and stopall command handlers."""
    aid = automation._admin_user_id

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^stop\s+["']""", from_users=aid,
    ))
    async def _stop(event):
        if not automation.is_admin(event):
            return

        stop_text = parse_stop_command(event.raw_text)
        if stop_text is None:
            active_here = automation.loops.in_chat(event.chat_id)
            if active_here:
                loop_list = "\n".join(
                    f'  • `stop "{k[1]}"`' for k in active_here
                )
                await event.reply(
                    f"⚠️ Usage: `stop \"TEXT\"`\n\nActive:\n{loop_list}"
                )
            else:
                await event.reply(
                    "⚠️ Usage: `stop \"TEXT\"`\n\nℹ️ No active loops here."
                )
            return

        key = make_loop_key(event.chat_id, stop_text)

        
        cancelled = await automation.loops.cancel_by_key(key)

        
        
        try:
            unregister_loop(event.chat_id, stop_text)
            removed_sends = unregister_bounded_sends_for_text(
                event.chat_id, stop_text,
            )
            logger.info(
                f"[STOP] Removed from state: chat={event.chat_id} "
                f'text="{stop_text}"; saved send jobs={removed_sends}'
            )
        except Exception as e:
            logger.error(f"[STOP] State unregister failed: {e}")

        if cancelled:
            await event.reply(
                f'🛑 Stopped loop for **"{stop_text}"** in this chat.'
            )
        else:
            active_here = automation.loops.in_chat(event.chat_id)
            if active_here:
                suggestions = "\n".join(
                    f'  • `stop "{k[1]}"`' for k in active_here
                )
                await event.reply(
                    f'ℹ️ No loop for **"{stop_text}"** here.\n\n'
                    f"Active:\n{suggestions}"
                )
            else:
                await event.reply(
                    f'ℹ️ No loop for **"{stop_text}"** here. '
                    f"No loops running."
                )

    
    @automation.main_client.on(events.NewMessage(
        pattern=r'^stopall$', from_users=aid,
    ))
    async def _stopall(event):
        if not automation.is_admin(event):
            return

        all_keys = list(automation.loops.active.keys())
        count = await automation.loops.cancel_all() if all_keys else 0

        
        for key in all_keys:
            try:
                unregister_loop(key[0], key[1])
            except Exception as e:
                logger.error(f"[STOPALL] State unregister failed: {e}")

        saved_count = unregister_all_bounded_sends()

        if not count and not saved_count:
            await event.reply("ℹ️ No active loops.")
            return

        logger.info(
            f"[STOPALL] Stopped {count} loop(s) and cleared "
            f"{saved_count} saved send job(s)"
        )

        await event.reply(
            f"🛑 Stopped **{count}** loop(s) and cleared "
            f"**{saved_count}** saved send job(s) globally."
        )

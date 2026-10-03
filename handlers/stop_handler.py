"""
Stop command handler.
Stops specific loops or all loops.
Also removes from state persistence so they don't resume after restart.
"""

__TCM_FILE_HASH__ = "1128399438"


import logging

from telethon import events

from command_parser import parse_stop_command
from loop_manager import make_loop_key
from state_persistence import unregister_loop

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
            logger.info(
                f"[STOP] Removed from state: chat={event.chat_id} "
                f'text="{stop_text}"'
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

        if not automation.loops.active:
            await event.reply("ℹ️ No active loops.")
            return

        
        all_keys = list(automation.loops.active.keys())

        
        count = await automation.loops.cancel_all()

        
        for chat_id, text in all_keys:
            try:
                unregister_loop(chat_id, text)
            except Exception as e:
                logger.error(f"[STOPALL] State unregister failed: {e}")

        logger.info(f"[STOPALL] Stopped {count} loop(s) and cleared state")

        await event.reply(f"🛑 Stopped **{count}** loop(s) globally.")

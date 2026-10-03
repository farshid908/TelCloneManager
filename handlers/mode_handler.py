"""Telethon commands for switching between Clone Mode and Normal Mode."""

__TCM_FILE_HASH__ = "3391620050"


import logging
import re

from telethon import events

logger = logging.getLogger("TG-Auto")

MODE_COMMAND = re.compile(
    r"^/(clone|normal)\s+(?:mod|mode)\s+(on|off)\s*$",
    re.IGNORECASE,
)


def _progress_bar(done, total, width=12):
    if total <= 0:
        return "░" * width + " 0%"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    return f"{'█' * filled}{'░' * (width - filled)} {int(ratio * 100)}%"


async def _notify_mode_switch(automation, target_mode):
    """Notify the admin in the bot PV and update one message in place."""
    try:
        from clone_settings.bot_core import get_bot_client

        bot = get_bot_client()
        admin_id = getattr(automation, "_admin_user_id", None)
        if bot is None or admin_id is None:
            return

        total = len(getattr(automation, "clone_names", []))
        status = await bot.send_message(
            admin_id,
            f"Activating {'Normal Mode' if target_mode == 'normal' else 'Clone Mode'}...\n"
            f"In progress\n{_progress_bar(0, total)}",
        )

        async def progress(index, name, phase, completed, progress_total):
            marker = "🔄" if phase == "running" else "✅" if phase == "done" else "⏭"
            try:
                await status.edit_text(
                    f"Activating {'Normal Mode' if target_mode == 'normal' else 'Clone Mode'}...\n"
                    f"In progress\n"
                    f"Clone{index}...{marker}\n"
                    f"{_progress_bar(completed, progress_total)}"
                )
            except Exception as exc:
                logger.debug("[MODE] Status edit skipped: %s", exc)

        return status, progress
    except Exception as exc:
        logger.warning("[MODE] Could not create Bot API status: %s", exc)
        return None, None


def register_mode_handler(automation):
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=MODE_COMMAND,
        from_users=aid,
    ))
    async def _mode(event):
        if not automation.is_admin(event):
            return

        match = MODE_COMMAND.match(event.raw_text.strip())
        if not match:
            return

        requested_mode = match.group(1).lower()
        requested_action = match.group(2).lower()
        
        
        target_mode = requested_mode
        if requested_action == "off":
            target_mode = "normal" if requested_mode == "clone" else "clone"

        try:
            await event.delete()
        except Exception as exc:
            logger.debug("[MODE] Could not delete command: %s", exc)

        status, progress = await _notify_mode_switch(automation, target_mode)
        try:
            from clone_settings.profile_modes import switch_mode

            await switch_mode(
                automation,
                target_mode,
                progress_callback=progress,
            )
        except Exception as exc:
            logger.error("[MODE] Switch to %s failed: %s", target_mode, exc, exc_info=True)
            if status is not None:
                try:
                    await status.edit_text(
                        f"Failed to activate "
                        f"{'Normal Mode' if target_mode == 'normal' else 'Clone Mode'}."
                    )
                except Exception:
                    pass


"""Telethon commands for granting and revoking Normal Mode menu access."""

__TCM_FILE_HASH__ = "3415762665"


import logging
import re

from telethon import events

from clone_settings.bot_core import get_bot_username
from clone_settings.normal_access import grant_with_info, get_access, revoke


logger = logging.getLogger("TG-Auto")

GRANT_PATTERN = re.compile(r"^accessed\s+normal\s+mod\s+menu\s*$", re.I)
REVOKE_PATTERN = re.compile(
    r"^deac\w*\s+normal\s+mod\s+menu\s*$",
    re.I,
)


def register_access_handler(automation):
    admin_id = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=GRANT_PATTERN,
    ))
    async def _grant_access(event):
        if not event.is_private or not automation.is_admin(event):
            return

        
        
        user_id = event.chat_id
        if user_id is None:
            return
        current = get_access()
        if current is not None and str(current["user_id"]) != str(user_id):
            try:
                await event.delete()
            except Exception:
                logger.debug("[ACCESS] Could not delete duplicate grant command",
                             exc_info=True)
            try:
                from clone_settings.bot_core import get_admin_user_id, get_bot_client
                bot = get_bot_client()
                admin_id = get_admin_user_id()
                if bot is not None and admin_id is not None:
                    label = current.get("name") or "Unknown user"
                    username = current.get("username")
                    if username:
                        label += f" (@{username})"
                    await bot.send_message(
                        admin_id,
                        "You cannot grant Normal Mode access to two users "
                        "at the same time.\n\n"
                        "Revoke access from this user first:\n"
                        f"Name: {label}\n"
                        f"Chat ID: {current['user_id']}",
                    )
            except Exception:
                logger.debug("[ACCESS] Could not notify duplicate grant",
                             exc_info=True)
            return

        try:
            chat = await event.get_chat()
            name = " ".join(
                part for part in (
                    getattr(chat, "first_name", None),
                    getattr(chat, "last_name", None),
                ) if part
            ) or "Unknown user"
            info = {
                "name": name,
                "username": getattr(chat, "username", None) or "",
            }
        except Exception:
            info = {"name": "Unknown user", "username": ""}
        grant_with_info(user_id, info)
        username = get_bot_username() or "the Clone Manager bot"
        text = (
            f"Please start @{username} to access the Normal Mode menu."
        )
        try:
            await event.edit(text)
        except Exception:
            try:
                await event.reply(text)
            except Exception:
                logger.debug("[ACCESS] Could not send grant instruction",
                             exc_info=True)

    @automation.main_client.on(events.NewMessage(
        pattern=REVOKE_PATTERN,
    ))
    async def _revoke_access(event):
        if not event.is_private or not automation.is_admin(event):
            return
        revoke(event.chat_id)
        try:
            await event.delete()
        except Exception:
            logger.debug("[ACCESS] Could not delete revoke command",
                         exc_info=True)

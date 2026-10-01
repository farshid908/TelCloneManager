"""Handler for reporting the current chat ID to Saved Messages."""

import logging

from telethon import events
from telethon.tl.types import User, Chat, Channel

logger = logging.getLogger("TG-Auto")


def _entity_kind(entity) -> str:
    if isinstance(entity, User):
        return "private user"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


def register_chatid_handler(automation):
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"^get\s+chatid\s*$",
        from_users=aid,
    ))
    async def _get_chatid(event):
        if not automation.is_admin(event):
            return

        chat_id = event.chat_id
        try:
            entity = await event.get_chat()
            title = getattr(entity, "title", None)
            username = getattr(entity, "username", None)
            kind = _entity_kind(entity)
        except Exception as exc:
            logger.warning("[CHATID] Entity lookup failed: %s", exc)
            title = None
            username = None
            kind = "unknown"

        lines = [
            "Chat ID",
            f"ID: `{chat_id}`",
            f"Type: `{kind}`",
        ]
        if title:
            lines.append(f"Title: {title}")
        if username:
            lines.append(f"Username: `@{username}`")
        text = "\n".join(lines)

        try:
            await automation.main_client.send_message("me", text)
            await event.edit(f"✅ Chat ID sent to Saved Messages: `{chat_id}`")
        except Exception as exc:
            logger.error("[CHATID] Failed to save/edit result: %s", exc)
            try:
                await event.edit(f"⚠️ Could not send Chat ID: `{chat_id}`")
            except Exception:
                pass

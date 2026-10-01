"""Main-only notification suppression for selected groups/chats."""

import logging
import re

from telethon import events, utils

logger = logging.getLogger("TG-Auto")

_MUTE_PATTERN = re.compile(r"^/?mute(?:\s+(.+?))?\s*$", re.IGNORECASE)
_MUTE_LIST_PATTERN = re.compile(r"^/?mute\s+list\s*$", re.IGNORECASE)


def _muted_chats(automation):
    chats = getattr(automation, "_muted_main_chats", None)
    if chats is None:
        chats = set()
        automation._muted_main_chats = chats
    return chats


async def _mark_read(client, entity, max_id=None):
    """Acknowledge messages without sending a Telegram chat response."""
    await client.send_read_acknowledge(entity, max_id=max_id)


async def _is_reply_to_pinned(event):
    if not event.is_group or not event.reply_to_msg_id:
        return False
    try:
        replied = await event.get_reply_message()
        return bool(replied and getattr(replied, "pinned", False))
    except Exception:
        return False


def register_mute_handler(automation):
    """Register the Main-only mute command and passive read acknowledgements."""
    aid = automation._admin_user_id
    client = automation.main_client

    @client.on(events.NewMessage(
        pattern=r"^/?mute(?:\s+.+?)?\s*$",
        from_users=aid,
    ))
    async def _mute_command(event):
        match = _MUTE_PATTERN.match((event.raw_text or "").strip())
        if not match or not automation.is_admin(event):
            return

        target = (match.group(1) or "").strip()
        if target.lower() == "list":
            return
        try:
            if target:
                entity = await client.get_entity(target)
                chat_id = utils.get_peer_id(entity)
            elif event.is_group:
                entity = await event.get_input_chat()
                chat_id = int(event.chat_id)
            else:
                # Without a target, mute is meaningful only in a group.
                return

            _muted_chats(automation).add(int(chat_id))
            await _mark_read(client, entity, event.id)
            logger.info("[MUTE] Main enabled notification suppression for %s", chat_id)
        except Exception as exc:
            logger.warning("[MUTE] Could not mute %s: %s", target or event.chat_id, exc)

    @client.on(events.NewMessage(
        pattern=r"^/?mute\s+list\s*$",
        from_users=aid,
    ))
    async def _mute_list(event):
        if not _MUTE_LIST_PATTERN.match((event.raw_text or "").strip()):
            return
        if not automation.is_admin(event):
            return

        muted = sorted(_muted_chats(automation))
        if not muted:
            await event.edit(
                "🔇 Mute List\n\nNo muted groups or channels.",
                parse_mode=None,
            )
            return

        lines = ["🔇 Mute List", ""]
        for index, chat_id in enumerate(muted, 1):
            try:
                entity = await client.get_entity(chat_id)
                title = (
                    getattr(entity, "title", None)
                    or getattr(entity, "first_name", None)
                    or "Unknown"
                )
                username = getattr(entity, "username", None)
                link = f"https://t.me/{username}" if username else "Unavailable"
                lines.extend([
                    f"{index}. {title}",
                    f"Chat ID: {chat_id}",
                    f"Join link: {link}",
                    "",
                ])
            except Exception as exc:
                logger.warning("[MUTE] Could not resolve muted chat %s: %s", chat_id, exc)
                lines.extend([
                    f"{index}. Unknown",
                    f"Chat ID: {chat_id}",
                    "Join link: Unavailable",
                    "",
                ])

        report = "\n".join(lines).rstrip()
        if len(report) <= 4096:
            await event.edit(report, parse_mode=None)
            return

        # Keep the command itself edited, then send the remaining rows.
        chunks = []
        current = []
        current_len = 0
        for block in report.split("\n\n"):
            extra = len(block) + (2 if current else 0)
            if current and current_len + extra > 3900:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            current.append(block)
            current_len += len(block) + (2 if len(current) > 1 else 0)
        if current:
            chunks.append("\n\n".join(current))

        await event.edit(chunks[0], parse_mode=None)
        for chunk in chunks[1:]:
            await event.respond(chunk, parse_mode=None)

    @client.on(events.NewMessage())
    async def _suppress_notifications(event):
        if not event.is_group or not event.chat_id:
            return
        if int(event.chat_id) not in _muted_chats(automation):
            return

        try:
            mentioned = bool(getattr(event.message, "mentioned", False))
            pinned_reply = await _is_reply_to_pinned(event)
            if mentioned or pinned_reply:
                await _mark_read(client, await event.get_input_chat(), event.id)
                logger.debug(
                    "[MUTE] Marked Main notification as read in %s (mention=%s, pinned_reply=%s)",
                    event.chat_id,
                    mentioned,
                    pinned_reply,
                )
        except Exception as exc:
            logger.debug("[MUTE] Read acknowledgement failed: %s", exc)

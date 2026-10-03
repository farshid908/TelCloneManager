"""Handler for reporting Telegram chat IDs to Saved Messages."""

import logging
import re
from urllib.parse import parse_qs, urlsplit

from telethon import events, utils
from telethon.tl.types import User, Chat, Channel

logger = logging.getLogger("TG-Auto")

_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_NUMERIC_RE = re.compile(r"^-?\d+$")


def _clean_target(value):
    return (value or "").strip().strip("[]<>(){}.,;\"'")


def parse_chatid_target(value):
    """Return an entity target and optional message ID from user input."""
    clean = _clean_target(value)
    if not clean:
        return None

    if clean.startswith("tg://"):
        parsed = urlsplit(clean)
        if parsed.netloc.lower() != "resolve":
            return None
        query = parse_qs(parsed.query)
        domain = (query.get("domain") or [None])[0]
        post = (query.get("post") or [None])[0]
        if not domain or not _USERNAME_RE.fullmatch(domain):
            return None
        return {
            "entity": domain,
            "message_id": int(post) if post and post.isdigit() else None,
            "display": clean,
        }

    url_value = clean
    if re.match(r"^(?:www\.)?(?:t\.me|telegram\.me)/", url_value, re.I):
        url_value = "https://" + url_value

    if re.match(r"^https?://", url_value, re.I):
        parsed = urlsplit(url_value)
        if parsed.netloc.lower().removeprefix("www.") not in {
            "t.me",
            "telegram.me",
        }:
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            return None
        if parts[0].lower() in {"+", "joinchat"} or parts[0].startswith("+"):
            return None
        if parts[0].lower() == "c":
            if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
                return None
            return {
                "entity": int(f"-100{parts[1]}"),
                "message_id": int(parts[2]),
                "display": clean,
            }
        if parts[0].lower() == "s":
            if len(parts) < 2 or not _USERNAME_RE.fullmatch(parts[1]):
                return None
            message_id = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
            return {"entity": parts[1], "message_id": message_id, "display": clean}
        if not _USERNAME_RE.fullmatch(parts[0]):
            return None
        message_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
        return {"entity": parts[0], "message_id": message_id, "display": clean}

    if clean.startswith("@"):
        clean = clean[1:]
    if _NUMERIC_RE.fullmatch(clean):
        return {"entity": int(clean), "message_id": None, "display": value}
    if _USERNAME_RE.fullmatch(clean):
        return {"entity": clean, "message_id": None, "display": value}
    return None


async def _resolve_target(client, target):
    parsed = parse_chatid_target(target)
    if parsed is None:
        raise ValueError(
            "Use a username, public Telegram link, or a message link. "
            "Private invite links are not supported."
        )
    entity = await client.get_entity(parsed["entity"])
    message_id = parsed["message_id"]
    message = None
    if message_id is not None:
        message = await client.get_messages(entity, ids=message_id)
        if message is None:
            raise LookupError("The linked message was not found or is not accessible to Main.")
    chat_id = getattr(message, "chat_id", None) if message is not None else None
    if chat_id is None:
        chat_id = utils.get_peer_id(entity)
    return entity, int(chat_id), message_id


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
        pattern=r"^get\s+chatid(?:\s+\S+)?\s*$",
        from_users=aid,
    ))
    async def _get_chatid(event):
        if not automation.is_admin(event):
            return

        parts = (event.raw_text or "").strip().split(maxsplit=2)
        target = parts[2] if len(parts) == 3 else None
        if target is None:
            chat_id = event.chat_id
            try:
                entity = await event.get_chat()
            except Exception as exc:
                logger.warning("[CHATID] Entity lookup failed: %s", exc)
                entity = None
        else:
            try:
                entity, chat_id, message_id = await _resolve_target(
                    automation.main_client, target
                )
                logger.info(
                    "[CHATID] Resolved %s via Main: chat_id=%s message_id=%s",
                    target, chat_id, message_id
                )
            except Exception as exc:
                logger.warning("[CHATID] Target lookup failed for %r: %s", target, exc)
                error = f"⚠️ Could not resolve target with Main: {exc}"
                try:
                    await automation.main_client.send_message("me", error, parse_mode=None)
                    await event.edit(error, parse_mode=None)
                except Exception:
                    pass
                return
        title = getattr(entity, "title", None) if entity else None
        username = getattr(entity, "username", None) if entity else None
        kind = _entity_kind(entity) if entity else "unknown"

        lines = [
            "Chat ID",
            f"ID: {chat_id}",
            f"Type: {kind}",
        ]
        if target is not None:
            lines.append(f"Target: {target}")
        if title:
            lines.append(f"Title: {title}")
        if username:
            lines.append(f"Username: @{username}")
        text = "\n".join(lines)

        try:
            await automation.main_client.send_message("me", text, parse_mode=None)
            await event.edit(
                f"✅ Chat ID sent to Saved Messages: {chat_id}", parse_mode=None
            )
        except Exception as exc:
            logger.error("[CHATID] Failed to save/edit result: %s", exc)
            try:
                await event.edit(f"⚠️ Could not send Chat ID: {chat_id}", parse_mode=None)
            except Exception:
                pass

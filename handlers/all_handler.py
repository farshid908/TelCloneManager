"""Show groups and channels shared by Main and all clones."""

import asyncio
import html
import logging
import re

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import DeleteChatUserRequest
from telethon.tl.types import Channel, Chat
from config import BRIDGE_GROUP

logger = logging.getLogger("TG-Auto")


def _chat_key(entity):
    return type(entity).__name__, int(entity.id)


def _chat_id(entity):
    if isinstance(entity, Channel):
        return f"-100{entity.id}"
    return f"-{entity.id}"


def _public_link(entity):
    username = getattr(entity, "username", None)
    return f"https://t.me/{username}" if username else "_"


def _is_bridge_entity(entity):
    """Return True when an entity is the configured bridge group/channel."""
    if BRIDGE_GROUP is None:
        return False
    try:
        configured_id = int(BRIDGE_GROUP)
        entity_id = (
            int(_chat_id(entity))
            if isinstance(entity, (Chat, Channel))
            else None
        )
        return entity_id == configured_id
    except (TypeError, ValueError):
        return False


def _format_clone_numbers(numbers):
    numbers = sorted(set(numbers))
    if not numbers:
        return "None"
    parts = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        parts.append(
            f"Clone{start}-{previous}"
            if previous > start else f"Clone{start}"
        )
        start = previous = number
    parts.append(
        f"{start}-{previous}" if previous > start else f"{previous}"
    )
    first = parts[0]
    if first.startswith("Clone"):
        return first + (" " + " ".join(parts[1:]) if len(parts) > 1 else "")
    return "Clone" + " ".join(parts)


def _split_message(text, limit=3900):
    """Split a long report without breaking individual chat rows."""
    chunks = []
    current = []
    length = 0
    for line in text.splitlines():
        extra = len(line) + (1 if current else 0)
        if current and length + extra > limit:
            chunks.append("\n".join(current))
            current = []
            length = 0
        current.append(line)
        length += len(line) + (1 if len(current) > 1 else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


async def _leave_entity(client, entity):
    if isinstance(entity, Channel):
        await client(LeaveChannelRequest(entity))
        return True
    if isinstance(entity, Chat):
        me = await client.get_me()
        await client(DeleteChatUserRequest(chat_id=entity.id, user_id=me.id))
        return True
    return False


def register_all_handler(automation):
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"^/?all(?:\s+back)?$",
        from_users=aid,
    ))
    async def _all(event):
        try:
            if event.raw_text.strip().lower() in {"/all back", "all back"}:
                await event.edit(
                    "Leaving all clone accounts from groups and channels...\n"
                    "Please wait...",
                    parse_mode=None,
                )
                clients = list(zip(
                    automation.clone_names,
                    automation.clone_clients,
                ))
                success = failed = 0
                for name, client in clients:
                    try:
                        async for dialog in client.iter_dialogs():
                            entity = dialog.entity
                            if not isinstance(entity, (Chat, Channel)):
                                continue
                            if _is_bridge_entity(entity):
                                logger.info(
                                    "[ALL-BACK] Skipping bridge for %s",
                                    name,
                                )
                                continue
                            try:
                                await _leave_entity(client, entity)
                                success += 1
                            except FloodWaitError as exc:
                                await asyncio.sleep(exc.seconds + 1)
                                await _leave_entity(client, entity)
                                success += 1
                            except Exception as exc:
                                failed += 1
                                logger.warning(
                                    "[ALL-BACK] %s failed for %s: %s",
                                    name,
                                    getattr(entity, "title", entity.id),
                                    exc,
                                )
                    except Exception as exc:
                        failed += 1
                        logger.warning("[ALL-BACK] %s scan failed: %s", name, exc)
                await event.edit(
                    f"✅ All clones left their groups and channels.\n"
                    f"Success: {success}\nFailed: {failed}",
                    parse_mode=None,
                )
                return

            await event.edit(
                "Fetching chat details...\nPlease wait...",
                parse_mode=None,
            )
            clients = list(automation.clone_clients)
            memberships = {}
            for fallback_index, (client, clone_name) in enumerate(
                zip(clients, automation.clone_names),
                start=1,
            ):
                match = re.search(r"(\d+)$", str(clone_name))
                clone_index = int(match.group(1)) if match else fallback_index
                current = {}
                async for dialog in client.iter_dialogs():
                    entity = dialog.entity
                    if isinstance(entity, (Chat, Channel)):
                        current[_chat_key(entity)] = entity
                for key, entity in current.items():
                    memberships.setdefault(key, {
                        "entity": entity,
                        "clones": [],
                    })["clones"].append(clone_index)

            lines = ["Groups/channels and clone memberships:", ""]
            for item in sorted(
                memberships.values(),
                key=lambda value: getattr(value["entity"], "title", ""),
            ):
                entity = item["entity"]
                title = getattr(entity, "title", str(entity.id))
                lines.append(
                    f"<code>{_chat_id(entity)}</code> - "
                    f"{html.escape(str(title))} - "
                    f"{html.escape(_public_link(entity))} - "
                    f"{_format_clone_numbers(item['clones'])}"
                )
                lines.append("----------------------")
            if not memberships:
                lines.append("No groups or channels found.")
            report_chunks = _split_message("\n".join(lines))
            await event.edit(report_chunks[0], parse_mode="html")
            for chunk in report_chunks[1:]:
                await event.reply(chunk, parse_mode="html")
        except Exception as exc:
            logger.error("[ALL] Failed: %s", exc, exc_info=True)
            await event.edit(
                f"Failed to inspect shared chats: {type(exc).__name__}",
                parse_mode=None,
            )

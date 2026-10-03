"""Administrator-only /back command handler."""

__TCM_FILE_HASH__ = "5812410129"


import asyncio
import logging
import re

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import DeleteChatUserRequest
from telethon.tl.types import Channel, Chat, User

from entity_resolver import resolve_entity

logger = logging.getLogger("TG-Auto")


class _BotStatus:
    """One progress bar mirrored through Bot API recipients."""

    def __init__(self, bot, message_ids):
        self.bot = bot
        self.message_ids = message_ids

    async def edit(self, text):
        for chat_id, message_id in self.message_ids.items():
            try:
                await self.bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=message_id,
                )
            except Exception as exc:
                if "message is not modified" not in str(exc).lower():
                    logger.debug("[BACK] Bot status edit failed for %s: %s", chat_id, exc)

    async def delete(self):
        for chat_id, message_id in self.message_ids.items():
            try:
                await self.bot.delete_message(chat_id, message_id)
            except Exception:
                logger.debug("[BACK] Bot status delete failed", exc_info=True)


class _NullStatus:
    async def edit(self, _text):
        return None

    async def delete(self):
        return None


async def _create_bot_status(automation, text):
    """Create status bars only in the Aiogram bot chat(s)."""
    try:
        from clone_settings.bot_core import get_bot_client, get_admin_user_id

        bot = get_bot_client()
        recipients = [get_admin_user_id()]
        from temp_main_manager import temp_main
        if temp_main.active:
            recipients.append(temp_main.temp_user_id)
        recipients = list(dict.fromkeys(int(x) for x in recipients if x is not None))
        if bot is None or not recipients:
            return _NullStatus()

        message_ids = {}
        for chat_id in recipients:
            message = await bot.send_message(chat_id, text)
            message_ids[chat_id] = message.message_id
        return _BotStatus(bot, message_ids)
    except Exception:
        logger.exception("[BACK] Could not create Bot API status")
        return _NullStatus()


def _parse_target(raw_text):
    value = (raw_text or "").strip()
    if not value:
        return None
    match = re.search(
        r"https?://t\.me/(?:c/(?P<channel>\d+)|(?P<username>[A-Za-z0-9_]+))"
        r"(?:/\d+)?",
        value,
        re.IGNORECASE,
    )
    if match:
        if match.group("channel"):
            return int(f"-100{match.group('channel')}")
        return match.group("username")
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def _title(entity):
    return (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or "this group"
    )


def _progress(title, names, done, current=None, final=False):
    if final:
        return (
            f"✅ {title}: ✓{done['success']} "
            f"⏭{done['already']} ✗{done['failed']}"
        )
    total = len(names)
    percent = int(len(done["names"]) * 100 / total) if total else 100
    width = 20
    filled = round(width * percent / 100)
    bar = f"[{'█' * filled}{'░' * (width - filled)}] {percent}%"
    line = current if current else "Preparing..."
    return f"🔙Leaving {title} with {total} clone(s)…\n{line}\n{bar}"


async def _leave_one(client, target, name):
    try:
        
        
        
        entity = None
        target_text = str(target).strip() if target is not None else ""
        target_id = None
        if target_text.lstrip("-").isdigit():
            numeric = int(target_text)
            if numeric <= -1000000000000:
                target_id = abs(numeric + 1000000000000)
            else:
                target_id = abs(numeric)

        async for dialog in client.iter_dialogs():
            candidate = dialog.entity
            if not isinstance(candidate, (Channel, Chat)):
                continue
            if target_id is not None and int(candidate.id) == target_id:
                entity = candidate
                break
            username = getattr(candidate, "username", None)
            if target_id is None and username and (
                username.casefold() == target_text.lstrip("@").casefold()
            ):
                entity = candidate
                break

        if entity is None:
            
            
            if target_id is None:
                entity = await client.get_entity(target)
            else:
                raise ValueError("target is not present in this account's dialogs")
        if isinstance(entity, Channel):
            await client(LeaveChannelRequest(entity))
            return "success"
        if isinstance(entity, Chat):
            me = await client.get_me()
            await client(DeleteChatUserRequest(
                chat_id=entity.id,
                user_id=me.id,
            ))
            return "success"
        return "failed"
    except FloodWaitError as exc:
        await asyncio.sleep(exc.seconds + 1)
        return await _leave_one(client, target, name)
    except Exception as exc:
        logger.warning("[BACK] %s failed: %s", name, exc)
        return "failed"


async def _resolve_back_target(automation, target, event):
    """Best-effort title lookup; leaving itself is per-clone and independent."""
    if target is None:
        return await event.get_chat(), None

    try:
        entity = await resolve_entity(
            automation.main_client, target, "Main", silent=True,
        )
        if entity is not None and not isinstance(entity, User):
            return entity, None
    except Exception as exc:
        logger.info("[BACK] Main could not resolve %s: %s", target, exc)

    for client, name in zip(automation.clone_clients, automation.clone_names):
        try:
            target_text = str(target).strip()
            target_id = abs(int(target_text) + 1000000000000) if (
                target_text.lstrip("-").isdigit()
                and int(target_text) <= -1000000000000
            ) else (abs(int(target_text)) if target_text.lstrip("-").isdigit() else None)
            async for dialog in client.iter_dialogs():
                entity = dialog.entity
                if not isinstance(entity, (Chat, Channel)):
                    continue
                if target_id is not None and int(entity.id) == target_id:
                    return entity, None
                if target_id is None and getattr(entity, "username", "").casefold() == target_text.lstrip("@").casefold():
                    return entity, None
        except Exception as exc:
            logger.info("[BACK] %s could not resolve %s: %s", name, target, exc)

    return None, None
async def run_back_job(automation, target, title, status_message):
    names = list(automation.clone_names)
    done = {"names": [], "success": 0, "already": 0, "failed": 0}
    for client, name in zip(automation.clone_clients, names):
        try:
            await status_message.edit(
                _progress(title, names, done, f"{name}...🔄")
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("[BACK] Could not update progress: %s", exc)
        result = await _leave_one(client, target, name)
        done["names"].append(name)
        done[result] += 1
        try:
            await status_message.edit(
                _progress(title, names, done, f"{name}...✅")
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("[BACK] Could not update progress: %s", exc)

    final_text = _progress(title, names, done, final=True)
    try:
        await status_message.edit(final_text)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("[BACK] Could not update final report: %s", exc)
    await asyncio.sleep(10)
    try:
        await status_message.delete()
    except Exception:
        logger.debug("[BACK] Could not delete result", exc_info=True)


def _log_back_task_result(task):
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("[BACK] Job stopped: %s", error, exc_info=error)


def register_back_handler(automation):
    """Register /back for the administrator in any chat."""
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"^/back(?:\s+.+)?$",
        from_users=aid,
    ))
    async def _back(event):
        if not automation.is_admin(event):
            return

        raw_target = event.raw_text.strip()[5:].strip()
        if not raw_target and event.is_reply:
            replied = await event.get_reply_message()
            raw_target = (replied.text or "") if replied else ""
        target = _parse_target(raw_target)
        target_entity, resolve_error = await _resolve_back_target(
            automation, target, event,
        )
        if target_entity is None:
            logger.info("[BACK] Main/clone title lookup failed for %s; continuing per-clone scan", target)

        
        
        
        target_ref = target if target is not None else event.chat_id

        try:
            await event.delete()
        except Exception:
            logger.debug("[BACK] Could not delete command", exc_info=True)

        names = list(automation.clone_names)
        status = await _create_bot_status(
            automation,
            _progress(
                _title(target_entity) if target_entity is not None else str(target),
                names,
                {"names": [], "success": 0, "already": 0, "failed": 0},
            ),
        )
        task = asyncio.create_task(
            run_back_job(
                automation,
                target_ref,
                _title(target_entity) if target_entity is not None else str(target),
                status,
            )
        )
        task.add_done_callback(_log_back_task_result)

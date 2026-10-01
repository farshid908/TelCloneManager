"""Persistent clone join job with Bot API progress updates."""

import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path

from telethon import events
from telethon.errors import (
    ChannelPrivateError,
    ChatWriteForbiddenError,
    FloodWaitError,
    InviteRequestSentError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UserNotParticipantError,
    UserAlreadyParticipantError,
    UserBannedInChannelError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import (
    ImportChatInviteRequest,
    SendInlineBotResultRequest,
)
from telethon.tl.types import InputReplyToMessage

from command_parser import extract_join_target
from entity_resolver import resolve_entity

logger = logging.getLogger("TG-Auto")
JOIN_STATE_FILE = (
    Path(__file__).resolve().parents[1] / "state" / "go_join_state.json"
)
_pending_normal_go = {}
_active_join_tasks = set()
_active_temp_go_targets = set()
_temp_go_target_lock = asyncio.Lock()


class _BotStatus:
    def __init__(self, message):
        self.message = message

    async def edit(self, text):
        await self.message.edit_text(text, reply_markup=None)


def _serialize_inline_id(value):
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return value.to_dict() if hasattr(value, "to_dict") else None


def _deserialize_inline_id(value):
    if not isinstance(value, dict):
        return value
    kind = value.get("_")
    if kind in {"InputBotInlineMessageID", "inputBotInlineMessageID"}:
        from telethon.tl.types import InputBotInlineMessageID
        return InputBotInlineMessageID(
            dc_id=value["dc_id"],
            id=value["id"],
            access_hash=value["access_hash"],
        )
    if kind in {"InputBotInlineMessageID64", "inputBotInlineMessageID64"}:
        from telethon.tl.types import InputBotInlineMessageID64
        return InputBotInlineMessageID64(
            dc_id=value["dc_id"],
            owner_id=value["owner_id"],
            id=value["id"],
            access_hash=value["access_hash"],
        )
    return None


def _load_state():
    if not JOIN_STATE_FILE.is_file():
        return None
    try:
        with JOIN_STATE_FILE.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        logger.error("[GO] Could not load join state", exc_info=True)
        return None


def _save_state(state):
    JOIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = JOIN_STATE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, JOIN_STATE_FILE)


def _clear_state():
    try:
        JOIN_STATE_FILE.unlink(missing_ok=True)
    except OSError:
        logger.warning("[GO] Could not clear join state", exc_info=True)


def _bar(done, total, width=20):
    percent = int(done * 100 / total) if total else 100
    filled = round(width * percent / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {percent}%"


async def _join_one(client, target, name):
    try:
        if target["type"] == "invite":
            result = await client(ImportChatInviteRequest(hash=target["hash"]))
        else:
            source = target.get("channel_id", target.get("username"))
            entity = await resolve_entity(client, source, session_name=name)
            if not entity:
                return "failed"
            result = await client(JoinChannelRequest(entity))
        return "success" if result is not None else "already"
    except UserAlreadyParticipantError:
        return "already"
    except InviteRequestSentError:
        
        
        logger.info("[GO] %s join request submitted and is pending", name)
        return "pending"
    except FloodWaitError as exc:
        await asyncio.sleep(exc.seconds + 1)
        return await _join_one(client, target, name)
    except (
        InviteHashExpiredError,
        InviteHashInvalidError,
        ChannelPrivateError,
        ChatWriteForbiddenError,
        UserBannedInChannelError,
    ) as exc:
        logger.warning("[GO] %s failed: %s", name, exc)
        return "failed"
    except Exception as exc:
        logger.error("[GO] %s failed: %s", name, exc)
        return "failed"


async def _is_already_member(client, target):
    """Check membership before sending a join request."""
    try:
        
        
        
        if target.get("type") == "invite":
            return False

        source = target.get("channel_id", target.get("username"))
        if source is None:
            return False
        entity = await resolve_entity(client, source, silent=True)
        if entity is None:
            return False

        await client.get_permissions(entity, "me")
        return True
    except UserNotParticipantError:
        return False
    except (ChannelPrivateError, ValueError):
        return False
    except Exception as exc:
        logger.debug("[GO] Membership pre-check skipped: %s", exc)
        return False


def _progress_text(label, names, completed, current=None):
    total = len(names)
    line = f"{current}...🔄" if current else "Preparing..."
    return (
        f"🚀 Joining {label} with {total} clone(s)…\n"
        f"{line}\n"
        f"{_bar(len(completed), total)}"
    )


def _normal_progress_text(delay_range, names, completed, current=None,
                          remaining=0, finished=False):
    marker = "🔄✅" if finished else "🔄"
    line = (
        f"{current}...{marker} {max(0, int(remaining))}s"
        if current else "Preparing..."
    )
    return (
        f"normal join {delay_range[0]}-{delay_range[1]} 🎭\n"
        f"{line}\n"
        f"{_bar(len(completed), len(names))}"
    )


def _log_join_task_result(task):
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error("[GO] Join job stopped: %s", error, exc_info=error)


async def _edit_inline_status(client, peer, chat_id, message_id, text):
    """Edit the inline message with its Telegram inline-message ID."""
    inline_id = (
        _deserialize_inline_id(message_id.get("inline_id"))
        if isinstance(message_id, dict)
        else None
    )
    regular_id = (
        message_id.get("message_id")
        if isinstance(message_id, dict)
        else message_id
    )
    if inline_id:
        try:
            await client.edit_message(inline_id, text=text)
            return message_id
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("[GO] Inline status edit failed: %s", exc)
    elif regular_id:
        try:
            await client.edit_message(peer, regular_id, text=text)
            return message_id
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                logger.warning("[GO] Regular status edit failed: %s", exc)
    return message_id


async def _send_inline_status(client, bot_username, peer, reply_to_id,
                              target_text, label, clone_count):
    results = await client.inline_query(
        bot_username,
        f"go {target_text}",
        entity=peer,
    )
    if not results:
        raise RuntimeError("Clone Manager returned no inline Join result")
    result = results[0]
    request = SendInlineBotResultRequest(
        peer=await client.get_input_entity(peer),
        query_id=result._query_id,
        id=result.result.id,
        reply_to=InputReplyToMessage(reply_to_msg_id=reply_to_id),
    )
    updates = await client(request)
    message = client._get_response_message(request, updates, peer)
    message_id = getattr(message, "id", None)
    if message_id is None:
        message_id = getattr(updates, "id", None)
    if message_id is None:
        for update in getattr(updates, "updates", []):
            candidate = getattr(getattr(update, "message", None), "id", None)
            if candidate is not None:
                message_id = candidate
                break
    if message_id is None:
        raise RuntimeError("Telegram did not return the sent inline message")
    inline_id = None
    for update in getattr(updates, "updates", []):
        if getattr(update, "_", None) == "UpdateBotInlineSend":
            inline_id = getattr(update, "msg_id", None)
            break
    if inline_id is not None:
        return {
            "message_id": message_id,
            "inline_id": _serialize_inline_id(inline_id),
        }

    
    
    
    try:
        await client.delete_messages(peer, message_id)
    except Exception:
        logger.debug("[GO] Could not remove non-editable inline status",
                     exc_info=True)
    regular = await client.send_message(
        peer,
        _progress_text(label, ["clone"] * clone_count, {}),
        reply_to=reply_to_id,
    )
    return {
        "message_id": regular.id,
        "inline_id": None,
    }


async def run_join_job(
    automation,
    target,
    label,
    status_chat_id=None,
    status_message_id=None,
    reply_to_id=None,
    target_text=None,
    original_event=None,
    normal_delay=None,
    bot_status=None,
    bot_status_persistent=False,
    bot_message_id=None,
    bot_chat_id=None,
    bot_message_ids=None,
):
    from clone_settings.bot_core import get_bot_username

    client = automation.main_client
    if client is None or not client.is_connected():
        logger.warning("[GO] Main Telethon client is unavailable")
        return

    saved = _load_state() or {}
    names = list(automation.clone_names)
    clients = list(automation.clone_clients)
    completed = saved.get("completed", {})
    results = saved.get(
        "results",
        {"success": 0, "already": 0, "pending": 0, "failed": 0},
    )
    results.setdefault("success", 0)
    results.setdefault("already", 0)
    results.setdefault("pending", 0)
    results.setdefault("failed", 0)
    state = {
        "target": target,
        "target_text": target_text or label,
        "label": label,
        "names": names,
        "completed": completed,
        "results": results,
        "chat_id": status_chat_id,
        "message_id": status_message_id or saved.get("message_id"),
        "bot_status": bool(bot_status) or saved.get("bot_status", False),
        "bot_status_persistent": bot_status_persistent or saved.get(
            "bot_status_persistent", False
        ),
        "bot_message_id": bot_message_id or saved.get("bot_message_id"),
        "bot_chat_id": bot_chat_id or saved.get("bot_chat_id"),
        "bot_message_ids": bot_message_ids or saved.get("bot_message_ids", {}),
        "normal_delay": normal_delay or saved.get("normal_delay"),
        "delay_deadline": saved.get("delay_deadline"),
    }
    peer = None
    
    
    
    if not state["bot_status"] and not state.get("bot_status_persistent"):
        peer = await client.get_input_entity(state["chat_id"])
    if not state["bot_status"] and isinstance(state["message_id"], int):
        logger.warning("[GO] Discarding legacy join message state")
        state["message_id"] = None
    if not state["bot_status"] and (
        isinstance(state["message_id"], dict)
        and state["message_id"].get("message_id")
        and not state["message_id"].get("inline_id")
    ):
        logger.warning("[GO] Discarding incomplete join message state")
        state["message_id"] = None
    if (
        not state["bot_status"]
        and not state.get("bot_status_persistent")
        and not state["message_id"]
    ):
        state["message_id"] = await _send_inline_status(
            client,
            get_bot_username(),
            peer,
            reply_to_id,
            state["target_text"],
            label,
            len(names),
        )
    if state["bot_message_id"] is None and state.get("bot_status_persistent"):
        from clone_settings.bot_core import get_bot_client
        bot = get_bot_client()
        if bot is not None:
            recipients = state.get("bot_message_ids") or {}
            if recipients:
                for recipient_id, message_id in list(recipients.items()):
                    if message_id:
                        continue
                    status_message = await bot.send_message(
                        int(recipient_id),
                        _progress_text(label, names, completed),
                    )
                    recipients[str(recipient_id)] = status_message.message_id
                state["bot_message_ids"] = recipients
                
                
                first_id, first_message = next(iter(recipients.items()))
                state["bot_chat_id"] = int(first_id)
                state["bot_message_id"] = first_message
            else:
                status_message = await bot.send_message(
                    state["bot_chat_id"] or state["chat_id"],
                    _progress_text(label, names, completed),
                )
                state["bot_message_id"] = status_message.message_id
    _save_state(state)

    async def edit_status(text):
        if state["bot_status"]:
            if bot_status is not None:
                try:
                    await bot_status.edit(text)
                except Exception:
                    logger.debug("[GO] Bot status edit failed", exc_info=True)
            else:
                from clone_settings.bot_core import get_bot_client
                bot = get_bot_client()
                if bot is not None and state.get("chat_id") and state.get("message_id"):
                    try:
                        await bot.edit_message_text(
                            text,
                            chat_id=state["chat_id"],
                            message_id=state["message_id"],
                        )
                    except Exception:
                        logger.debug("[GO] Resumed bot status edit failed",
                                     exc_info=True)
            return
        if state.get("bot_status_persistent"):
            from clone_settings.bot_core import get_bot_client
            bot = get_bot_client()
            if bot is not None:
                recipients = state.get("bot_message_ids") or {}
                if not recipients and state.get("bot_message_id"):
                    recipients = {
                        str(state["bot_chat_id"] or state["chat_id"]):
                        state["bot_message_id"]
                    }
                for recipient_id, message_id in recipients.items():
                    try:
                        await bot.edit_message_text(
                            text,
                            chat_id=int(recipient_id),
                            message_id=message_id,
                        )
                    except Exception:
                        logger.debug(
                            "[GO] Persistent status edit failed for %s",
                            recipient_id,
                            exc_info=True,
                        )
            return
        await _edit_inline_status(
            automation.main_client, peer, state["chat_id"],
            state["message_id"], text,
        )

    completed_count = len(completed)
    for clone_client, name in zip(clients, names):
        if name in completed:
            continue
        if state["normal_delay"] and completed_count > 0:
            if not state.get("delay_deadline"):
                low, high = state["normal_delay"]
                state["delay_seconds"] = random.randint(int(low), int(high))
                state["delay_deadline"] = time.time() + state["delay_seconds"]
                _save_state(state)
            while True:
                remaining = max(0, int(state["delay_deadline"] - time.time()))
                await edit_status(_normal_progress_text(
                    state["normal_delay"], names, completed, name, remaining
                ))
                if remaining <= 0:
                    break
                
                
                await asyncio.sleep(min(30, remaining))
            state["delay_deadline"] = None
            _save_state(state)
        await edit_status(
            _normal_progress_text(
                state["normal_delay"], names, completed, name, 0
            ) if state["normal_delay"] else _progress_text(
                label, names, completed, name
            )
        )
        if await _is_already_member(clone_client, target):
            result = "already"
            logger.info("[GO] %s already belongs to the target; skipping join", name)
        else:
            result = await _join_one(clone_client, target, name)
        completed[name] = result
        results[result] += 1
        completed_count += 1
        state["completed"] = completed
        state["results"] = results
        _save_state(state)
        marker = {
            "success": "✅",
            "already": "⏭",
            "pending": "⏳",
            "failed": "❌",
        }.get(result, "❌")
        await edit_status(
            _normal_progress_text(
                state["normal_delay"], names, completed, name, 0, True
            ) if state["normal_delay"] else (
                f"🚀 Joining {label} with {len(names)} clone(s)…\n"
                f"{name}...{marker}\n"
                f"{_bar(len(completed), len(names))}"
            )
        )
        await asyncio.sleep(0.15)

    final_text = (
        f"✅ {label}: ✓{results['success']} "
        f"⏭{results['already']} ⏳{results['pending']} "
        f"✗{results['failed']}"
    )
    await edit_status(final_text)
    _clear_state()
    await asyncio.sleep(10)
    try:
        if state["bot_status"]:
            if bot_status is not None:
                if not state.get("bot_status_persistent"):
                    await bot_status.message.delete()
            else:
                from clone_settings.bot_core import get_bot_client
                bot = get_bot_client()
                if bot is not None and not state.get("bot_status_persistent"):
                    await bot.delete_message(
                        chat_id=state["chat_id"],
                        message_id=state["message_id"],
                    )
        elif state.get("bot_status_persistent"):
            from clone_settings.bot_core import get_bot_client
            bot = get_bot_client()
            if bot is not None:
                recipients = state.get("bot_message_ids") or {}
                if not recipients and state.get("bot_message_id"):
                    recipients = {
                        str(state["bot_chat_id"] or state["chat_id"]):
                        state["bot_message_id"]
                    }
                for recipient_id, message_id in recipients.items():
                    try:
                        await bot.delete_message(
                            chat_id=int(recipient_id), message_id=message_id,
                        )
                    except Exception:
                        logger.debug(
                            "[GO] Could not delete status for %s",
                            recipient_id,
                            exc_info=True,
                        )
        else:
            regular_id = (
                state["message_id"].get("message_id")
                if isinstance(state["message_id"], dict)
                else state["message_id"]
            )
            if regular_id:
                await automation.main_client.delete_messages(peer, regular_id)
    except Exception:
        logger.debug("[GO] Could not delete final status", exc_info=True)


async def resume_join_job(automation):
    state = _load_state()
    if not state:
        return
    if not set(automation.clone_names).intersection(state.get("names", [])):
        return
    logger.warning("[GO] Resuming interrupted join job")
    await run_join_job(
        automation,
        state["target"],
        state["label"],
        status_chat_id=state.get("chat_id"),
        status_message_id=state.get("message_id"),
        target_text=state.get("target_text"),
        normal_delay=state.get("normal_delay"),
        bot_message_id=state.get("bot_message_id"),
        bot_chat_id=state.get("bot_chat_id"),
    )


def track_join_task(task):
    """Track a join task so Temporary Main can cancel it safely."""
    _active_join_tasks.add(task)
    task.add_done_callback(_active_join_tasks.discard)
    task.add_done_callback(_log_join_task_result)
    return task


async def cancel_all_join_jobs():
    """Cancel all active /go jobs and remove their resume state."""
    tasks = list(_active_join_tasks)
    _active_join_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _pending_normal_go.clear()
    _active_temp_go_targets.clear()
    _clear_state()
    logger.info("[GO] Cancelled all active join jobs")


def parse_go_request(raw_text, reply_text=None):
    """Parse /go target; numeric chat IDs are deliberately rejected."""
    raw = (raw_text or "").strip()
    command_match = re.match(r"^/go(?:@\w+)?(?:\s+|$)", raw, re.IGNORECASE)
    args = raw[command_match.end():].strip() if command_match else raw
    target_text = args or (reply_text or "").strip()
    if not target_text or target_text.lstrip("-").isdigit():
        return None
    target = extract_join_target(target_text)
    if not target:
        return None
    return target, target_text


async def start_temp_main_go(automation, target, target_text, recipient_ids):
    """Start a /go job requested by Temporary Main with two Bot statuses."""
    from clone_settings.bot_core import get_bot_client

    target_key = str(target_text or "").strip().casefold()
    async with _temp_go_target_lock:
        if target_key in _active_temp_go_targets:
            logger.warning(
                "[GO] Ignoring duplicate Temporary Main request for %s",
                target_text,
            )
            return False
        _active_temp_go_targets.add(target_key)

    bot = get_bot_client()
    recipients = []
    for recipient_id in recipient_ids:
        if recipient_id is not None and int(recipient_id) not in recipients:
            recipients.append(int(recipient_id))
    if bot is None or not recipients:
        logger.warning("[GO] Cannot start Temporary Main job: Bot API unavailable")
        _active_temp_go_targets.discard(target_key)
        return False

    label = target.get("hash") or target.get("username", "target")
    initial = _progress_text(
        label, list(automation.clone_names), {},
    )
    bot_message_ids = {}
    try:
        for recipient_id in recipients:
            message = await bot.send_message(recipient_id, initial)
            bot_message_ids[str(recipient_id)] = message.message_id
    except Exception:
        _active_temp_go_targets.discard(target_key)
        logger.exception(
            "[GO] Could not create Temporary Main status for %s",
            target_text,
        )
        return False

    async def _run_temp_go():
        try:
            await run_join_job(
                automation,
                target,
                label,
                target_text=target_text,
                bot_status_persistent=True,
                bot_message_ids=bot_message_ids,
                bot_chat_id=recipients[0],
            )
        finally:
            _active_temp_go_targets.discard(target_key)

    try:
        task = asyncio.create_task(_run_temp_go(), name="temp-main-go")
    except Exception:
        _active_temp_go_targets.discard(target_key)
        raise
    track_join_task(task)
    logger.info(
        "[GO] Temporary Main started join for %s; statuses=%s",
        target_text, recipients,
    )
    return True


async def handle_go_callback(event, data, automation):
    match = re.fullmatch(r"go:normal:(yes|no)", data)
    if not match:
        return False
    from clone_settings.bot_core import can_operate
    if not can_operate(event.sender_id):
        await event.answer()
        return True
    request = _pending_normal_go.pop(event.sender_id, None)
    if match.group(1) == "no" or request is None:
        await event.edit("Normal join cancelled.", buttons=None)
        await event.answer()
        return True
    from clone_settings.profile_modes import switch_mode
    status = _BotStatus(event._query.message)
    total_clones = len(getattr(automation, "clone_names", []))
    await event.edit(
        "In progress\nStarting Normal Mode...\n"
        + _bar(0, total_clones),
        buttons=None,
    )
    await event.answer()

    async def progress(index, name, phase, completed, total):
        marker = "🔄" if phase == "running" else "✅"
        await status.edit(
            f"In progress\nClone{index}...{marker}\n{_bar(completed, total)}"
        )

    await switch_mode(automation, "normal", progress_callback=progress)
    await run_join_job(
        automation, request["target"], request["label"],
        status_chat_id=event.sender_id,
        status_message_id=event._query.message.message_id,
        target_text=request["target_text"],
        normal_delay=request["normal_delay"],
        bot_status=status,
    )
    return True


def register_go_handler(automation):
    """Keep /go admin-only while reporting the job through Bot API."""
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"^/go(?:\s+.+)?$", from_users=aid,
    ))
    async def _go(event):
        if not automation.is_admin(event):
            return
        raw_args = event.raw_text.strip()[3:].strip()
        normal_delay = None
        delay_match = re.fullmatch(
            r"normal\s+(\d+)\s*-\s*(\d+)", raw_args, re.IGNORECASE
        )
        if delay_match:
            normal_delay = tuple(sorted((
                int(delay_match.group(1)),
                int(delay_match.group(2)),
            )))
            raw_target = ""
        else:
            raw_target = raw_args
        if not raw_target and event.is_reply:
            reply_msg = await event.get_reply_message()
            raw_target = (reply_msg.text or "") if reply_msg else ""
        
        
        if raw_target.lstrip("-").isdigit():
            return
        target = (
            {"type": "channel_id", "channel_id": int(raw_target)}
            if raw_target.startswith("-100")
            else extract_join_target(raw_target)
        )
        if not target:
            return
        label = target.get("hash") or target.get("username", "?")
        if not event.is_reply:
            return
        replied = await event.get_reply_message()
        if replied is None:
            return
        from clone_settings.bot_core import get_bot_client
        bot = get_bot_client()
        if bot is None or automation._admin_user_id is None:
            logger.warning("[GO] Bot API is unavailable for PV status")
            return
        if normal_delay and not automation.normal_mode:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            _pending_normal_go[automation._admin_user_id] = {
                "target": target,
                "label": label,
                "target_text": raw_target or label,
                "normal_delay": normal_delay,
            }
            await bot.send_message(
                automation._admin_user_id,
                "⚠️ Normal Mode is OFF. Do you want to enable it?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="Yes✅", callback_data="go:normal:yes"
                    ),
                    InlineKeyboardButton(
                        text="No❌", callback_data="go:normal:no"
                    ),
                ]]),
            )
            try:
                await event.delete()
            except Exception:
                pass
            return
        try:
            await event.delete()
        except Exception:
            logger.debug("[GO] Could not delete command message", exc_info=True)
        status_message = await bot.send_message(
            automation._admin_user_id,
            _normal_progress_text(
                normal_delay, list(automation.clone_names), {},
                automation.clone_names[0] if normal_delay and automation.clone_names else None,
                0,
            ) if normal_delay else (
                f"🚀 Joining {label} with {len(automation.clone_names)} clone(s)…\n"
                f"Preparing...\n{_bar(0, len(automation.clone_names))}"
            ),
        )
        task = asyncio.create_task(
            run_join_job(
                automation,
                target,
                label,
                status_chat_id=event.chat_id,
                status_message_id=None if not normal_delay else status_message.message_id,
                reply_to_id=replied.id,
                target_text=raw_target,
                normal_delay=normal_delay,
                bot_status=_BotStatus(status_message) if normal_delay else None,
                bot_status_persistent=not bool(normal_delay),
                bot_message_id=status_message.message_id,
                bot_chat_id=automation._admin_user_id,
            )
        )
        track_join_task(task)

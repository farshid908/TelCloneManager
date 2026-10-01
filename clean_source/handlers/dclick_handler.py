"""Persistent button disappearance click commands."""

import asyncio
import logging
import re

from telethon import events
from telethon.errors import BotResponseTimeoutError

from click_engine import find_matching_button
from state_persistence import (
    register_hunt_click,
    unregister_hunt_click,
    update_hunt_click_cursor,
    update_hunt_click_progress,
    get_all_hunt_clicks,
)

logger = logging.getLogger("TG-Auto")

_DCLICK_JOBS = {}
_DCLICK_CONFIGS = {}

_COMMAND_RE = re.compile(
    r"^/?(?P<loop>loop)?dclick(?P<count>\d+)\s+"
    r"(?P<q>['\"])(?P<button>.+?)(?P=q)"
    r"(?:\s+/(?P<delay>\d+))?\s*$",
    re.IGNORECASE,
)

_STOP_RE = re.compile(
    r"^/?stoploopdclick\s+(?P<q>['\"])(?P<button>.+?)(?P=q)\s*$",
    re.IGNORECASE,
)


async def cancel_all_dclick_jobs():
    """Cancel LoopDclick jobs and remove their persisted state."""
    jobs = list(_DCLICK_JOBS.values())
    configs = list(_DCLICK_CONFIGS.values())
    _DCLICK_JOBS.clear()
    _DCLICK_CONFIGS.clear()
    for task in jobs:
        if not task.done():
            task.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)
    for config in configs:
        state_id = config.get("state_id")
        if state_id:
            unregister_hunt_click(state_id)
    logger.info("[DCLICK] Cancelled all LoopDclick jobs")


def _sessions(automation):
    return [
        (automation.main_client, "Main"),
        *zip(automation.clone_clients, automation.clone_names),
    ]


async def _click_once(client, chat_id, message_id, target, session_name):
    try:
        message = await client.get_messages(chat_id, ids=message_id)
        if not message or not message.buttons:
            return False
        match = find_matching_button(message, target)
        if match is None:
            return False
        row, col, button, _, _ = match
        try:
            await message.click(row, col)
        except BotResponseTimeoutError:
            
            
            
            logger.warning(
                '[DCLICK] [%s] Click response timed out for message %s; '
                "counting the request as submitted",
                session_name,
                message_id,
            )
        logger.info(
            '[DCLICK] [%s] Clicked "%s" on message %s',
            session_name,
            (button.text or "").strip(),
            message_id,
        )
        return True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug(
            "[DCLICK] [%s] Click failed on message %s: %s",
            session_name,
            message_id,
            exc,
        )
        return False


async def _run_for_message(automation, chat_id, message_id, config, event_key=None):
    sessions = _sessions(automation)
    if not sessions:
        return

    start = config["cursor"] if config["loop"] else 0
    start = start % len(sessions)
    count = config["count"]
    delay = config["delay"]
    state_id = config.get("state_id")
    config["active_message_id"] = message_id
    config["active_account_index"] = start
    config["active_clicks"] = 0
    if state_id:
        update_hunt_click_progress(state_id, 0, 0)

    try:
        for offset in range(len(sessions)):
            index = (start + offset) % len(sessions)
            client, name = sessions[index]
            clicked = 0
            disappeared = False
            config["active_account_index"] = index

            for attempt in range(count):
                ok = await _click_once(
                    client, chat_id, message_id, config["button"], name
                )
                if not ok:
                    disappeared = True
                    break
                clicked += 1
                config["active_clicks"] = clicked
                if state_id:
                    update_hunt_click_progress(state_id, clicked, delay)
                if attempt + 1 < count:
                    await asyncio.sleep(delay)

            if clicked == 0 and disappeared:
                
                
                if config["loop"]:
                    config["cursor"] = (index + 1) % len(sessions)
                    config["active_clicks"] = 0
                    update_hunt_click_cursor(state_id, config["cursor"])
                    update_hunt_click_progress(state_id, 0, 0)
                continue

            if disappeared:
                logger.info(
                    "[DCLICK] [%s] Target disappeared after %s click(s)",
                    name,
                    clicked,
                )
                if config["loop"]:
                    config["cursor"] = index
                    config["active_clicks"] = 0
                    update_hunt_click_cursor(state_id, index)
                    update_hunt_click_progress(state_id, 0, 0)
                return

            logger.info(
                "[DCLICK] [%s] Target survived %s clicks; trying next session",
                name,
                count,
            )
            if config["loop"]:
                config["cursor"] = (index + 1) % len(sessions)
                config["active_clicks"] = 0
                update_hunt_click_cursor(state_id, config["cursor"])
                update_hunt_click_progress(state_id, 0, 0)

        logger.info("[DCLICK] Target survived all configured sessions")
    finally:
        config["active_message_id"] = None
        config["active_account_index"] = None
        config["active_clicks"] = 0


def register_dclick_handler(automation):
    aid = automation._admin_user_id
    jobs = _DCLICK_JOBS
    configs = _DCLICK_CONFIGS

    async def _delete_later(message, delay=5):
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            logger.debug("[DCLICK] Could not delete status message", exc_info=True)

    def _task_done(task, chat_id):
        if jobs.get(chat_id) is task:
            jobs.pop(chat_id, None)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except Exception as exc:
            logger.error("[DCLICK] Task inspection failed for %s: %s", chat_id, exc)
            return
        if error is not None:
            logger.error(
                "[DCLICK] Task failed in chat %s: %s",
                chat_id,
                error,
                exc_info=(type(error), error, error.__traceback__),
            )

    @automation.main_client.on(events.NewMessage(
        pattern=r"(?i)^/?(?:loop)?dclick\d+\s+",
        from_users=aid,
    ))
    async def _command(event):
        if not automation.is_admin(event):
            return
        match = _COMMAND_RE.match((event.raw_text or "").strip())
        if not match:
            logger.debug("[DCLICK] Pattern matched but command parsing failed: %r", event.raw_text)
            return

        logger.info("[DCLICK] Command received: %s", event.raw_text.strip())

        chat_id = int(event.chat_id)
        old = jobs.pop(chat_id, None)
        if old and not old.done():
            old.cancel()
        old_config = configs.pop(chat_id, None)
        if old_config and old_config.get("state_id"):
            unregister_hunt_click(old_config["state_id"])

        config = {
            "loop": bool(match.group("loop")),
            "count": int(match.group("count")),
            "delay": int(match.group("delay") or 0),
            "button": match.group("button").strip(),
            "cursor": 0,
            
            "command_message_id": int(event.id),
        }
        if config["count"] <= 0:
            logger.warning("[DCLICK] Invalid click count: %s", config["count"])
            return

        if config["loop"]:
            config["state_id"] = register_hunt_click(
                session_ref="loopdclick",
                clone_index=config["cursor"],
                times=config["count"],
                button_text=config["button"],
                delay=config["delay"],
                loop_mode=True,
                chat_id=chat_id,
                cursor=0,
                kind="dclick",
            )
        configs[chat_id] = config

        try:
            await event.edit(
                f'{"LoopDclick" if match.group("loop") else "Dclick"}'
                f'{match.group("count")} "{match.group("button").strip()}"'
                f' /{int(match.group("delay") or 0)}\n'
                "🔄 Dclick is active...",
                parse_mode=None,
            )
        except Exception as exc:
            logger.debug("[DCLICK] Could not edit command: %s", exc)
        logger.info(
            '[DCLICK] Armed in chat %s: %sDclick%s "%s" every %ss; '
            "waiting for a new target message",
            chat_id,
            "loop" if config["loop"] else "",
            config["count"],
            config["button"],
            config["delay"],
        )

    def _event_key(event):
        edit_date = getattr(event, "edit_date", None)
        edit_value = edit_date.isoformat() if edit_date else ""
        raw = getattr(event, "raw_text", None) or ""
        return (int(event.id), edit_value, raw)

    def _schedule_target(event, config):
        key = _event_key(event)
        seen = config.setdefault("seen_events", set())
        if key in seen:
            return
        seen.add(key)
        
        
        if len(seen) > 256:
            seen.pop()
        chat_id = int(event.chat_id)
        previous = jobs.get(chat_id)
        if previous and not previous.done():
            return
        task = asyncio.create_task(
            _run_for_message(automation, chat_id, int(event.id), config, key)
        )
        jobs[chat_id] = task
        task.add_done_callback(
            lambda completed, target_chat=chat_id:
            _task_done(completed, target_chat)
        )

    @automation.main_client.on(events.NewMessage(
        pattern=r"(?i)^/?stoploopdclick\s+",
        from_users=aid,
    ))
    async def _stop_command(event):
        if not automation.is_admin(event):
            return
        match = _STOP_RE.match((event.raw_text or "").strip())
        if not match:
            return

        chat_id = int(event.chat_id)
        requested = match.group("button").strip()
        config = configs.get(chat_id)
        task = jobs.get(chat_id)
        active = (
            config is not None
            and config.get("loop")
            and config.get("button", "").casefold() == requested.casefold()
            and (
                (task is not None and not task.done())
                or config.get("state_id")
            )
        )

        if not active:
            text = (
                f'❌ No active LoopDclick for "{requested}" '
                f'in this chat.'
            )
            try:
                await event.edit(text, parse_mode=None)
            except Exception:
                return
            asyncio.create_task(_delete_later(event))
            return

        if task is not None and not task.done():
            task.cancel()
        jobs.pop(chat_id, None)
        configs.pop(chat_id, None)
        if config.get("state_id"):
            unregister_hunt_click(config["state_id"])
        try:
            await event.edit(
                f'🛑 LoopDclick "{requested}" stopped in this chat.',
                parse_mode=None,
            )
        except Exception:
            return
        asyncio.create_task(_delete_later(event))

    @automation.main_client.on(events.NewMessage())
    async def _new_message(event):
        chat_id = event.chat_id
        config = configs.get(chat_id)
        if config is None or not event.is_group:
            return
        if event.id == config.get("command_message_id"):
            return
        _schedule_target(event, config)

    @automation.main_client.on(events.MessageEdited())
    async def _edited_message(event):
        chat_id = event.chat_id
        config = configs.get(chat_id)
        if config is None or not event.is_group:
            return
        if event.id == config.get("command_message_id"):
            return
        _schedule_target(event, config)


async def resume_persisted_dclicks(automation):
    """Restore LoopDclick configurations; next group message triggers them."""
    restored = 0
    for task_id, info in get_all_hunt_clicks().items():
        if info.get("kind") != "dclick":
            continue
        chat_id = info.get("chat_id")
        if chat_id is None:
            continue
        _DCLICK_CONFIGS[int(chat_id)] = {
            "loop": True,
            "count": int(info.get("times") or 1),
            "delay": int(info.get("delay") or 0),
            "button": info.get("button_text", ""),
            "cursor": int(info.get("cursor", 0)),
            "state_id": task_id,
            "command_message_id": None,
            "seen_events": set(),
        }
        restored += 1
    if restored:
        logger.info("[DCLICK] Restored %s LoopDclick configuration(s)", restored)

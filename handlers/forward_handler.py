"""Forward trigger messages to a bot and send a selected response line."""

import asyncio
import html
import json
import logging
import os
import random

from telethon import events

from command_parser import parse_forward_line_command
from entity_resolver import resolve_entity

logger = logging.getLogger("TG-Auto")
_jobs = {}
_job_meta = {}
_STATE_FILE = "sessions/_forward_state.json"


class _ResumeStatus:
    async def edit(self, *_args, **_kwargs):
        return None


def _accounts(automation):
    return [(automation.main_client, "Main")] + list(
        zip(automation.clone_clients, automation.clone_names)
    )


def _line(text, number):
    lines = (text or "").splitlines()
    return lines[number - 1].strip() if len(lines) >= number else None


def _read_state():
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(chat_id, state):
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        data = _read_state()
        data[str(chat_id)] = state
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        logger.exception("[FORWARD] Could not save state")


def _delete_state(chat_id):
    try:
        data = _read_state()
        data.pop(str(chat_id), None)
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("[FORWARD] Could not delete state")


async def cancel_all_forwards():
    """Cancel every forward job and remove its persisted state."""
    jobs = list(_jobs.values())
    _jobs.clear()
    _job_meta.clear()
    for task in jobs:
        if not task.done():
            task.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as file:
            json.dump({}, file)
    except Exception:
        logger.exception("[FORWARD] Could not clear persisted state")
    logger.info("[FORWARD] Cancelled all forward jobs")


def _is_code_message(message):
    return any(
        type(entity).__name__ in {"MessageEntityCode", "MessageEntityPre"}
        for entity in (getattr(message, "entities", None) or [])
    )


def _mono(text):
    """Render a whole status message as Telegram monospace text."""
    return f"<code>{html.escape(str(text), quote=False)}</code>"


async def _edit_and_delete(event, text, seconds=3):
    try:
        await event.edit(_mono(text), parse_mode="html")
        await asyncio.sleep(seconds)
        await event.delete()
    except Exception:
        logger.debug("[FORWARD] Could not edit/delete status message", exc_info=True)


async def _run_job(automation, parsed, target_chat_id, status_message):
    if await resolve_entity(automation.main_client, target_chat_id, "Main") is None:
        try:
            await status_message.edit("⚠️ گروه محل اجرای دستور قابل دسترسی نیست.")
        except Exception:
            logger.exception("[FORWARD] Could not update status message")
        return

    accounts = _accounts(automation)
    forward_account = parsed["account"]
    state = _read_state().get(str(target_chat_id), {}) if parsed["is_loop"] else {}
    output_index = int(state.get("output_index", 0)) % len(accounts)
    output_used = int(state.get("output_used", 0))
    started_accounts = set()
    lock = asyncio.Lock()
    stopped = asyncio.Event()

    def choose_account(selector):
        if selector == "random":
            return random.randrange(len(accounts))
        if selector == "main":
            return 0
        idx = int(selector[5:])
        return idx if 1 <= idx < len(accounts) else None

    async def handle_trigger(event):
        nonlocal output_index, output_used
        if event.chat_id != target_chat_id or event.sender_id != parsed["source_id"]:
            return
        if parsed["trigger"].casefold() not in (event.raw_text or "").casefold():
            return
        if _is_code_message(event.message) or lock.locked():
            return
        async with lock:
            forward_index = choose_account(forward_account)
            
            
            default_send = "clone1" if len(accounts) > 1 else "main"
            send_index = (
                output_index % len(accounts)
                if parsed["is_loop"]
                else choose_account(parsed["send_account"] or default_send)
            )
            if forward_index is None or send_index is None:
                logger.error("[FORWARD] Invalid account selector")
                return
            forward_client, forward_name = accounts[forward_index]
            send_client, send_name = accounts[send_index]
            bot = await resolve_entity(forward_client, parsed["user"], forward_name, silent=True)
            if bot is None:
                logger.error("[FORWARD] [%s] Cannot resolve %s", forward_name, parsed["user"])
                return

            if forward_name.casefold() not in started_accounts:
                try:
                    await forward_client.send_message(bot, "/start")
                    started_accounts.add(forward_name.casefold())
                    await asyncio.sleep(0.8)
                except Exception:
                    logger.exception("[FORWARD] [%s] /start failed", forward_name)

            reply_event = asyncio.Event()
            reply_box = {}
            bot_id = getattr(bot, "id", None)

            async def bot_handler(reply):
                if reply.chat_id == bot_id and reply.sender_id == bot_id:
                    reply_box["message"] = reply
                    reply_event.set()

            forward_client.add_event_handler(bot_handler, events.NewMessage(chats=bot))
            try:
                await forward_client.forward_messages(bot, event.message, from_peer=target_chat_id)
                logger.info(
                    "[FORWARD] [%s] Trigger forwarded to %s; output=%s",
                    forward_name, parsed["user"], send_name,
                )
                try:
                    await asyncio.wait_for(reply_event.wait(), timeout=max(30, parsed["delay"] * 3))
                except asyncio.TimeoutError:
                    logger.warning("[FORWARD] [%s] No response", forward_name)
                    return
                response = reply_box.get("message")
                selected = _line(getattr(response, "raw_text", None), parsed["line"])
                if not selected:
                    logger.warning("[FORWARD] [%s] No line %s", forward_name, parsed["line"])
                    return
                await send_client.send_message(target_chat_id, selected)
                if parsed["is_loop"]:
                    output_used += 1
                    if output_used >= parsed["batch"]:
                        output_used = 0
                        output_index = (output_index + 1) % len(accounts)
                    _write_state(target_chat_id, {
                        **parsed, "target_chat_id": target_chat_id,
                        "output_index": output_index, "output_used": output_used,
                    })
                else:
                    output_used += 1
                    if output_used >= parsed["batch"]:
                        stopped.set()
                        _delete_state(target_chat_id)
                await asyncio.sleep(parsed["delay"])
            finally:
                forward_client.remove_event_handler(bot_handler)

    automation.main_client.add_event_handler(handle_trigger, events.NewMessage())
    try:
        await stopped.wait()
    except asyncio.CancelledError:
        raise
    finally:
        automation.main_client.remove_event_handler(handle_trigger)
        current = asyncio.current_task()
        if _jobs.get(target_chat_id) is current:
            _jobs.pop(target_chat_id, None)
            _job_meta.pop(target_chat_id, None)


async def resume_persisted_forwards(automation):
    """Resume saved forwardloop jobs after the clients reconnect."""
    for key, saved in _read_state().items():
        if not saved.get("is_loop"):
            continue
        try:
            chat_id = int(saved.get("target_chat_id", key))
            parsed = {
                "mode": "forwardloop",
                "is_loop": True,
                "batch": int(saved["batch"]),
                "account": str(saved["account"]).lower(),
                "source_id": int(saved["source_id"]),
                "trigger": str(saved["trigger"]),
                "user": str(saved["user"]),
                "delay": int(saved["delay"]),
                "line": int(saved["line"]),
                "send_account": None,
                "no_trace": True,
            }
        except (KeyError, TypeError, ValueError):
            logger.warning("[FORWARD] Ignoring invalid saved state for %s", key)
            continue
        if chat_id in _jobs and not _jobs[chat_id].done():
            continue
        _job_meta[chat_id] = {**parsed, "target_chat_id": chat_id}
        _jobs[chat_id] = asyncio.create_task(
            _run_job(automation, parsed, chat_id, _ResumeStatus())
        )
        logger.info(
            "[FORWARD] Resumed loop in chat %s at output account index %s (%s/%s)",
            chat_id,
            saved.get("output_index", 0),
            saved.get("output_used", 0),
            parsed["batch"],
        )


def register_forward_handler(automation):
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(pattern=r"^forward(?:loop)?\d+\(", from_users=aid))
    async def _forward(event):
        if not automation.is_admin(event) or _is_code_message(event.message):
            return
        parsed = parse_forward_line_command(event.raw_text)
        if not parsed:
            await event.reply('⚠️ Usage: `forward10(main) ID "/pick" for @BOT /3 send(clone1) "line 1"`\nWithout send(...), finite mode starts from Clone1.\nLoop: `forwardloop10(main) ID "/pick" for @BOT /3 send "line 1"`')
            return
        target_chat_id = event.chat_id
        old = _jobs.get(target_chat_id)
        if old:
            old.cancel()
        text = f'🔁 Forward{" loop" if parsed["is_loop"] else ""} started ({parsed["account"]}, batch={parsed["batch"]})'
        if parsed["no_trace"]:
            msg = event
        else:
            try:
                await event.edit(text, parse_mode=None)
                msg = event
            except Exception:
                msg = await event.reply(text, parse_mode=None)
        _job_meta[target_chat_id] = {**parsed, "target_chat_id": target_chat_id}
        _jobs[target_chat_id] = asyncio.create_task(_run_job(automation, parsed, target_chat_id, msg))
        if parsed["no_trace"]:
            try:
                await event.delete()
            except Exception:
                pass

    @automation.main_client.on(events.NewMessage(pattern=r"^forward\s+-list$", from_users=aid))
    async def _forward_list(event):
        if not automation.is_admin(event) or _is_code_message(event.message):
            return
        lines = ["Forward loops"]
        for chat_id, meta in _job_meta.items():
            source_id = meta.get("source_id")
            lines += [
                "",
                f"Chat: {chat_id}",
                f"Mode: {'loop' if meta['is_loop'] else 'finite'}",
                f"Forward: {meta['account']}",
                f"Source: {source_id}",
                f"Trigger: {meta['trigger']}",
                f"BOT: {meta['user']}",
                f"Delay: {meta['delay']}s | Line: {meta['line']} | Batch: {meta['batch']}",
                f"Stop: stopforward {source_id} in {chat_id}",
            ]
        if len(lines) == 1:
            lines.append("\nNo active forward loops.")
        await event.edit(_mono("\n".join(lines)), parse_mode="html")

    @automation.main_client.on(events.NewMessage(pattern=r"^stopforward(?:\s+-?\d+(?:\s+in\s+-?\d+)?)?$", from_users=aid, func=None))
    async def _stop_forward(event):
        if not automation.is_admin(event) or _is_code_message(event.message):
            return
        parts = event.raw_text.split()
        source_id = None
        if len(parts) == 4 and parts[2].lower() == "in":
            source_id = int(parts[1])
            target = int(parts[3])
        else:
            target = int(parts[1]) if len(parts) == 2 else event.chat_id
        meta = _job_meta.get(target)
        if source_id is not None and (
            meta is None or int(meta.get("source_id", 0)) != source_id
        ):
            await _edit_and_delete(
                event,
                f"No forward loop found for source {source_id} in chat {target}.",
            )
            return
        task = _jobs.pop(target, None)
        if task:
            task.cancel()
            _job_meta.pop(target, None)
            _delete_state(target)
            await _edit_and_delete(
                event,
                f"Forward stopped: {source_id or meta.get('source_id', target)} in {target}",
            )
        else:
            await _edit_and_delete(event, f"No forward loop found in chat {target}.")

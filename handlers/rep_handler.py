"""Reply-trigger automation for Main, clones, and random account selection."""

__TCM_FILE_HASH__ = "7530465937"


import asyncio
import logging
import random
import re

from telethon import events

from command_parser import parse_rep_command
from state_persistence import (
    get_all_rep_jobs,
    register_rep_job,
    unregister_rep_job,
    update_rep_job,
)

logger = logging.getLogger("TG-Auto")

_rep_jobs = {}
_rep_job_meta = {}

REP_LIST_RE = re.compile(r"^/?rep\s+-list\s*$", re.IGNORECASE)
REP_STOP_RE = re.compile(
    r'''^(?:/?repall|/?reploop|/?rep\s+stop)\s+'''
    r'''(?P<q>["'])(?P<trigger>.+?)(?P=q)'''
    r'''(?:\s+(?P<chat_id>-?\d+))?\s*$''',
    re.IGNORECASE | re.DOTALL,
)


async def _edit_status(status_event, text):
    """Best-effort progress update on the original command message."""
    if status_event is None:
        return
    try:
        await status_event.edit(text, parse_mode=None)
    except Exception:
        
        
        logger.debug("[REP-CMD] Could not edit status message", exc_info=True)


def _accounts(automation):
    return [(automation.main_client, "Main")] + list(
        zip(automation.clone_clients, automation.clone_names)
    )


def _account_index(automation, selector):
    accounts = _accounts(automation)
    if isinstance(selector, str):
        selector = selector.lower()
        if selector == "m":
            selector = "main"
        elif selector.isdigit():
            selector = f"clone{int(selector)}"
    if selector == "main":
        return 0
    if selector == "random":
        return random.randrange(len(accounts))
    try:
        index = int(selector[5:])
    except (TypeError, ValueError):
        return None
    return index if 1 <= index < len(accounts) else None


def _choose_account(automation, selector):
    accounts = _accounts(automation)
    index = _account_index(automation, selector)
    return accounts[index] if index is not None else None


def _first_line(text, number):
    lines = (text or "").splitlines()
    return lines[number - 1].strip() if len(lines) >= number else None


def _event_text(event):
    """Return message text/caption, including media captions."""
    raw = getattr(event, "raw_text", None)
    if raw is not None:
        return raw
    message = getattr(event, "message", None)
    return getattr(message, "message", None) or ""


def _sender_id(message):
    value = getattr(message, "sender_id", None)
    if value is not None:
        return value
    sender = getattr(message, "from_id", None)
    return getattr(sender, "user_id", None) or getattr(sender, "channel_id", None)


def _reply_to_id(message):
    value = getattr(message, "reply_to_msg_id", None)
    if value is not None:
        return value
    reply = getattr(message, "reply_to", None)
    return getattr(reply, "reply_to_msg_id", None)


def _rep_job_key(source_chat, parsed):
    """Build a complete stable key so independent REP jobs never collide."""
    return "|".join([
        str(source_chat),
        str(parsed.get("mode", "rep")).casefold(),
        str(parsed.get("batch") or "1"),
        str(parsed.get("account", "")).casefold(),
        str(parsed.get("trigger_sender_id") or "any"),
        str(parsed.get("trigger", "")).casefold(),
        str(parsed.get("first_text", "")),
        str(parsed.get("reply_mode") or "send").casefold(),
        str(parsed.get("reply_account") or "same").casefold(),
        str(parsed.get("reply_sender_id") or "any"),
        str(parsed.get("line") or "1"),
    ])


async def cancel_all_rep_jobs():
    """Cancel all reply-trigger jobs, including Temporary Main jobs."""
    jobs = list(_rep_jobs.values())
    _rep_jobs.clear()
    _rep_job_meta.clear()
    for task in jobs:
        if not task.done():
            task.cancel()
    if jobs:
        await asyncio.gather(*jobs, return_exceptions=True)
    
    
    logger.info("[REP-CMD] Cancelled runtime rep tasks; checkpoints preserved")


async def start_rep_job(automation, parsed, status_event=None, restore=None):
    """Install one reply-trigger listener in the command's current chat."""
    
    
    source_chat = (
        getattr(status_event, "chat_id", None)
        if status_event is not None
        else None
    )
    if restore is not None:
        source_chat = restore.get("source_chat", source_chat)
    if source_chat is None:
        source_chat = parsed.get("chat_id")
    source_chat_name = None
    if status_event is not None:
        try:
            source_chat_name = getattr(await status_event.get_chat(), "title", None)
        except Exception:
            source_chat_name = None
    if restore is not None:
        source_chat_name = restore.get("source_chat_name", source_chat_name)
    job_key = str((restore or {}).get("job_key") or _rep_job_key(source_chat, parsed))
    trigger_sender_id = parsed.get("trigger_sender_id")
    old = _rep_jobs.pop(job_key, None)
    _rep_job_meta.pop(job_key, None)
    if old:
        old.cancel()
    if restore is None:
        unregister_rep_job(job_key)

    accounts = _accounts(automation)
    start_index = _account_index(automation, parsed["account"])
    if start_index is None:
        await _edit_status(status_event, "⚠️ Invalid account or clone number.")
        return False
    start_account_name = accounts[start_index][1]
    line_account_selector = parsed.get("reply_account")
    line_start_index = None
    if line_account_selector and line_account_selector != "random":
        line_start_index = _account_index(automation, line_account_selector)
        if line_start_index is None:
            await _edit_status(
                status_event,
                "⚠️ Invalid account or clone number for LINE sender.",
            )
            return False
    lock = asyncio.Lock()
    completed = int((restore or {}).get("completed", 0))
    account_send_counts = {
        str(key): int(value)
        for key, value in (restore or {}).get("account_send_counts", {}).items()
    }
    if parsed.get("mode") == "rep":
        max_triggers = parsed.get("batch")
    elif parsed.get("mode") == "repall":
        
        
        max_triggers = parsed.get("batch", 1) * len(accounts)
    else:
        max_triggers = None
    meta = {
        "mode": parsed["mode"],
        "batch": parsed.get("batch"),
        "account": parsed["account"],
        "source_chat": source_chat,
        "source_chat_name": source_chat_name,
        "job_key": job_key,
        "trigger": parsed["trigger"],
        "trigger_sender_id": trigger_sender_id,
        "first_text": parsed["first_text"],
        "delay": parsed.get("delay"),
        "reply_mode": parsed.get("reply_mode"),
        "reply_account": parsed.get("reply_account"),
        "reply_sender_id": parsed.get("reply_sender_id"),
        "line": parsed.get("line"),
        "response_timeout": parsed.get("response_timeout"),
        "completed": 0,
        "current_account": start_account_name,
        "state": "waiting_for_trigger",
        "current_account_index": None,
        "last_trigger_id": None,
        "last_wa_message_id": None,
        "last_response_message_id": None,
        "last_selected_line": None,
        "last_action": "restored_waiting_for_trigger" if restore else None,
        "account_send_counts": account_send_counts,
    }
    if restore:
        meta.update({
            key: value for key, value in restore.items()
            if key in {
                "started_at", "checkpoint_at", "current_account",
                "current_account_index", "last_trigger_id",
                "last_wa_message_id", "last_response_message_id",
                "last_selected_line", "last_action", "last_timeout",
            }
        })
        
        
        
        if restore.get("state") != "waiting_for_trigger":
            meta["last_interrupted_state"] = restore.get("state")
            meta["last_action"] = "restarted_waiting_for_new_trigger"
        meta["state"] = "waiting_for_trigger"
    meta["completed"] = completed

    def _checkpoint(**changes):
        meta.update(changes)
        update_rep_job(job_key, **changes)

    register_rep_job(
        job_key,
        {
            "source_chat": source_chat,
            "source_chat_name": source_chat_name,
            "job_key": job_key,
            "parsed": dict(parsed),
            **meta,
        },
    )

    status_lines = [
        "🔁 REP listener active",
        f"Source chat ID: {source_chat}",
        f"Start account: {start_account_name}",
        f"Trigger: {parsed['trigger']}",
        f"Trigger sender ID: {trigger_sender_id or 'ANY'}",
        f"Waiting for trigger: {parsed['trigger']}",
        f"Initial reply after trigger: {parsed['first_text']}",
    ]
    if parsed.get("mode") in {"reploop", "repall"}:
        status_lines.append(
            (
                f"Rotation: after {parsed['batch']} triggers → next account"
                if parsed.get("mode") == "reploop"
                else (
                    f"Rotation: after {parsed.get('batch', 1)} triggers "
                    f"→ next account, total {max_triggers}"
                )
            )
        )
    status_lines.extend([
        f"Expected responder ID: {parsed.get('reply_sender_id') or 'ANY'}",
        f"Line account: {parsed.get('reply_account') or 'same as /wa'}",
        f"Delay: {parsed.get('delay', 0)}s",
            (
                "First reply timeout: unlimited"
                if not parsed.get("response_timeout")
                else f"First reply timeout: {parsed['response_timeout']}s"
            ),
        f"Selected line: LINE {parsed.get('line') or 1}",
    ])
    await _edit_status(status_event, "\n".join(status_lines))
    
    
    status_event = None

    async def _job():
        finished = asyncio.Event()

        async def _on_source(event):
            nonlocal completed
            
            
            raw = _event_text(event)
            if event.chat_id != source_chat:
                return
            if trigger_sender_id is not None and event.sender_id != trigger_sender_id:
                return
            if parsed["trigger"].casefold() not in raw.casefold():
                return

            async with lock:
                if max_triggers is not None and completed >= max_triggers:
                    return
                if parsed.get("account") == "random":
                    
                    
                    account_index = random.randrange(len(accounts))
                elif parsed.get("mode") in {"reploop", "repall"}:
                    account_index = (start_index + completed // parsed["batch"]) % len(accounts)
                else:
                    account_index = start_index
                client, account_name = accounts[account_index]
                _checkpoint(
                    completed=completed,
                    current_account=account_name,
                    current_account_index=account_index,
                    last_trigger_id=event.id,
                    state="sending_initial_reply",
                    last_action="trigger_detected",
                )

                await _edit_status(
                    status_event,
                    "\n".join([
                        "🔔 Trigger detected",
                        f"Source chat ID: {source_chat}",
                        f"Trigger sender ID: {trigger_sender_id or 'ANY'}",
                        f"Trigger message ID: {event.id}",
                        f"Account: {account_name}",
                        f"Sending: {parsed['first_text']}",
                        (
                            f"Rotation: {completed + 1}/{parsed['batch']} "
                            f"on {account_name}"
                        ) if parsed.get("mode") == "reploop" else (
                            f"Progress: {completed + 1}/{max_triggers}"
                            if parsed.get("mode") == "repall" else ""
                        ),
                    ]),
                )

                has_reply_chain = (
                    parsed.get("reply_mode") is not None
                    and parsed.get("line") is not None
                )
                response_event = asyncio.Event()
                response_box = {}
                wa_message = None

                async def _on_response(response):
                    if response.chat_id != source_chat or wa_message is None:
                        return
                    if _reply_to_id(response) != wa_message.id:
                        return
                    response_sender_id = _sender_id(response)
                    logger.info(
                        "[REP-CMD] Reply candidate: sender_id=%s "
                        "reply_to=%s text=%r",
                        response_sender_id,
                        _reply_to_id(response),
                        _event_text(response)[:200],
                    )
                    expected = parsed.get("reply_sender_id")
                    if expected is not None and response_sender_id != expected:
                        return
                    response_box["event"] = response
                    response_event.set()

                
                
                if has_reply_chain:
                    client.add_event_handler(
                        _on_response,
                        events.NewMessage(chats=source_chat),
                    )
                try:
                    wa_message = await client.send_message(
                        source_chat,
                        parsed["first_text"],
                        reply_to=event.id,
                    )
                    completed += 1
                    account_key = account_name
                    account_send_counts[account_key] = (
                        account_send_counts.get(account_key, 0) + 1
                    )
                    _checkpoint(
                        completed=completed,
                        account_send_counts=dict(account_send_counts),
                        state=(
                            "waiting_for_response"
                            if has_reply_chain else "waiting_for_trigger"
                        ),
                        last_wa_message_id=wa_message.id,
                        last_action="initial_reply_sent",
                    )
                    logger.info(
                        "[REP-CMD] [%s] Replied in %s to trigger %s (%s/%s)",
                        account_name, source_chat, event.id, completed,
                        parsed.get("batch") or "∞",
                    )
                    await _edit_status(
                        status_event,
                        "\n".join([
                            "✅ Initial reply sent",
                            f"Source chat ID: {source_chat}",
                            f"Account: {account_name}",
                            f"Message ID: {wa_message.id}",
                            f"Text: {parsed['first_text']}",
                        ]),
                    )
                except Exception:
                    logger.error("[REP-CMD] Initial reply failed", exc_info=True)
                    if has_reply_chain:
                        client.remove_event_handler(_on_response)
                    return

                if not has_reply_chain:
                    if max_triggers is not None and completed >= max_triggers:
                        finished.set()
                    return

                try:
                    _checkpoint(
                        completed=completed,
                        state="waiting_for_response",
                        last_wa_message_id=wa_message.id,
                        last_action="waiting_for_response",
                    )
                    first_reply_timeout = parsed.get("response_timeout") or 0
                    first_deadline = (
                        asyncio.get_running_loop().time() + first_reply_timeout
                        if first_reply_timeout > 0
                        else None
                    )

                    
                    response = response_box.get("event")
                    first_reply_timed_out = False
                    while response is None:
                        try:
                            recent = await client.get_messages(
                                source_chat,
                                reply_to=wa_message.id,
                                limit=20,
                            )
                            candidates = recent if isinstance(recent, list) else [recent]
                            for candidate in candidates:
                                candidate_sender = _sender_id(candidate)
                                expected = parsed.get("reply_sender_id")
                                if (
                                    getattr(candidate, "id", None)
                                    and _reply_to_id(candidate) == wa_message.id
                                    and (expected is None or candidate_sender == expected)
                                ):
                                    response = candidate
                                    break
                        except Exception:
                            logger.debug("[REP-CMD] Could not fetch latest reply", exc_info=True)
                        if response is None:
                            if first_deadline is None:
                                await response_event.wait()
                                response_event.clear()
                                response = response_box.get("event")
                                continue

                            remaining = (
                                first_deadline - asyncio.get_running_loop().time()
                            )
                            if remaining <= 0:
                                first_reply_timed_out = True
                                break
                            try:
                                await asyncio.wait_for(
                                    response_event.wait(),
                                    timeout=remaining,
                                )
                            except asyncio.TimeoutError:
                                first_reply_timed_out = True
                                break
                            response_event.clear()
                            response = response_box.get("event")

                    if first_reply_timed_out:
                        
                        
                        _checkpoint(
                            state="waiting_for_trigger",
                            last_timeout=True,
                            last_action="response_timeout",
                        )
                        logger.info(
                            "[REP-CMD] [%s] No first reply within %ss; "
                            "skipping this cycle",
                            account_name,
                            first_reply_timeout,
                        )
                        await _edit_status(
                            status_event,
                            "⌛ No reply received\n"
                            f"Source chat ID: {source_chat}\n"
                            f"Account: {account_name}\n"
                            "This cycle was skipped; listener remains active.",
                        )
                        if max_triggers is not None and completed >= max_triggers:
                            finished.set()
                        return

                    
                    
                    await asyncio.sleep(parsed["delay"])
                    latest_response = response_box.get("event") or response
                    try:
                        recent = await client.get_messages(
                            source_chat,
                            reply_to=wa_message.id,
                            limit=20,
                        )
                        candidates = recent if isinstance(recent, list) else [recent]
                        for candidate in candidates:
                            candidate_sender = _sender_id(candidate)
                            expected = parsed.get("reply_sender_id")
                            if (
                                getattr(candidate, "id", None)
                                and _reply_to_id(candidate) == wa_message.id
                                and (expected is None or candidate_sender == expected)
                            ):
                                latest_response = candidate
                                break
                    except Exception:
                        logger.debug(
                            "[REP-CMD] Could not fetch final reply state",
                            exc_info=True,
                        )
                    response = latest_response

                    selected_line = _first_line(
                        _event_text(response), parsed.get("line") or 1
                    )
                    _checkpoint(
                        state="sending_selected_line",
                        last_response_message_id=response.id,
                        last_selected_line=selected_line,
                        last_action="response_received",
                    )
                    await _edit_status(
                        status_event,
                        "\n".join([
                            "📩 Response received",
                            f"Source chat ID: {source_chat}",
                            f"Responder ID: {parsed.get('reply_sender_id') or 'ANY'}",
                            f"Response message ID: {response.id}",
                            f"Selected line: {selected_line or '(not found)'}",
                        ]),
                    )
                    if selected_line:
                        line_client = client
                        line_account_name = account_name
                        if line_account_selector == "random":
                            line_client, line_account_name = random.choice(accounts)
                        elif line_start_index is not None:
                            if parsed.get("mode") in {"reploop", "repall"}:
                                line_index = (
                                    line_start_index
                                    + completed // parsed["batch"]
                                ) % len(accounts)
                            else:
                                line_index = line_start_index
                            line_client, line_account_name = accounts[line_index]
                        await line_client.send_message(
                            source_chat,
                            selected_line,
                        )
                        _checkpoint(
                            state="waiting_for_trigger",
                            last_action="selected_line_sent",
                            line_account=line_account_name,
                        )
                        await _edit_status(
                            status_event,
                            "\n".join([
                                "✅ Cycle complete",
                                f"Source chat ID: {source_chat}",
                                f"Responder ID: {parsed.get('reply_sender_id') or 'ANY'}",
                                f"Line account: {line_account_name}",
                                f"Sent line: {selected_line}",
                                (
                                    f"Progress: {completed}/{max_triggers}"
                                    if max_triggers is not None
                                    else f"Progress: {completed}"
                                ),
                            ]),
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.error("[REP-CMD] Reply-chain step failed", exc_info=True)
                finally:
                    if has_reply_chain:
                        client.remove_event_handler(_on_response)

                _checkpoint(
                    state="waiting_for_trigger",
                    last_action="cycle_complete",
                )

                if max_triggers is not None and completed >= max_triggers:
                    finished.set()

        automation.main_client.add_event_handler(
            _on_source,
            events.NewMessage(chats=source_chat),
        )
        try:
            if max_triggers is not None:
                await finished.wait()
            else:
                await asyncio.Event().wait()
        finally:
            automation.main_client.remove_event_handler(_on_source)
            if max_triggers is not None and completed >= max_triggers:
                unregister_rep_job(job_key)

    task = asyncio.create_task(_job(), name=f"rep-{job_key[:48]}")
    _rep_jobs[job_key] = task
    _rep_job_meta[job_key] = meta

    def _task_done(completed_task):
        if _rep_jobs.get(job_key) is not completed_task:
            return
        _rep_jobs.pop(job_key, None)
        _rep_job_meta.pop(job_key, None)
        if completed_task.cancelled():
            return
        try:
            error = completed_task.exception()
        except Exception:
            error = None
        if error is None:
            return
        checkpoint = get_all_rep_jobs().get(job_key)
        if not checkpoint or (
            max_triggers is not None
            and checkpoint.get("completed", 0) >= max_triggers
        ):
            return
        logger.error(
            "[REP-CMD] Listener crashed; scheduling recovery for %s",
            job_key,
            exc_info=(type(error), error, error.__traceback__),
        )
        asyncio.create_task(
            _recover_rep_job(automation, checkpoint),
            name=f"rep-recover-{job_key[:40]}",
        )

    task.add_done_callback(_task_done)
    return True


async def _recover_rep_job(automation, checkpoint):
    """Re-arm a persisted REP listener after an unexpected task failure."""
    await asyncio.sleep(1)
    job_id = checkpoint.get("job_key")
    if job_id in _rep_jobs:
        return
    parsed = checkpoint.get("parsed")
    if not isinstance(parsed, dict):
        logger.error("[REP-CMD] Cannot recover malformed job %s", job_id)
        return
    try:
        await start_rep_job(
            automation,
            parsed,
            status_event=None,
            restore=checkpoint,
        )
        logger.info("[REP-CMD] Re-armed recovered listener %s", job_id)
    except Exception:
        logger.error(
            "[REP-CMD] Listener recovery failed for %s",
            job_id,
            exc_info=True,
        )


async def resume_persisted_rep_jobs(automation):
    """Restore REP listeners without reading or replaying old Telegram messages."""
    restored = 0
    for job_id, checkpoint in get_all_rep_jobs().items():
        if job_id in _rep_jobs:
            continue
        source_chat = checkpoint.get("source_chat")
        parsed = checkpoint.get("parsed")
        if source_chat is None or not isinstance(parsed, dict):
            logger.warning("[REP-CMD] Dropping invalid persisted job: %s", job_id)
            unregister_rep_job(job_id)
            continue
        try:
            await start_rep_job(
                automation,
                parsed,
                status_event=None,
                restore=checkpoint,
            )
            restored += 1
            logger.info(
                "[REP-CMD] Restored chat=%s mode=%s progress=%s current=%s",
                source_chat,
                parsed.get("mode"),
                checkpoint.get("completed", 0),
                checkpoint.get("current_account"),
            )
        except Exception:
            logger.error(
                "[REP-CMD] Failed to restore persisted job %s",
                job_id,
                exc_info=True,
            )
    if restored:
        logger.info("[REP-CMD] Restored %s persisted REP job(s)", restored)


async def _list_rep_jobs(status_event):
    """Edit the command message with all active REP/REPLOOP jobs."""
    stale = [job_id for job_id, task in _rep_jobs.items() if task.done()]
    for job_id in stale:
        _rep_jobs.pop(job_id, None)
        _rep_job_meta.pop(job_id, None)

    if not _rep_job_meta:
        await _edit_status(status_event, "📋 REP jobs\n\nNo active rep/reploop/repall jobs.")
        return

    lines = [f"📋 REP jobs ({len(_rep_job_meta)})", ""]
    for index, (job_id, meta) in enumerate(_rep_job_meta.items(), 1):
        mode = meta.get("mode", "rep")
        mode_label = mode + (
            str(meta.get("batch"))
            if meta.get("batch") and (mode != "repall" or meta.get("batch") > 1)
            else ""
        )
        chat_name = meta.get("source_chat_name") or "Unknown chat"
        lines.extend([
            f"{mode_label} {meta.get('trigger')} {meta.get('first_text')} "
            f"in {meta.get('source_chat')} {chat_name}",
        ])
    await _edit_status(status_event, "\n".join(lines).rstrip())


async def _stop_rep_job(status_event, trigger, target_chat):
    """Cancel matching REP jobs in one source chat."""
    matching = [
        (job_id, meta) for job_id, meta in _rep_job_meta.items()
        if meta.get("source_chat") == target_chat
        and meta.get("trigger", "").strip().casefold() == trigger.strip().casefold()
    ]
    if not matching:
        await _edit_status(
            status_event,
            f'ℹ️ No active REP/REPLOOP job for "{trigger}" in chat {target_chat}.',
        )
        return False

    tasks = []
    for job_id, meta in matching:
        task = _rep_jobs.pop(job_id, None)
        _rep_job_meta.pop(job_id, None)
        unregister_rep_job(job_id)
        if task is not None and not task.done():
            task.cancel()
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    logger.info(
        "[REP-CMD] Stopped %s job: chat_id=%s trigger=%s",
        meta.get("mode", "rep").upper(),
        target_chat,
        trigger,
    )
    await _edit_status(
        status_event,
        f'🛑 Stopped {len(matching)} REP job(s)\n'
        f'Chat: {target_chat}\n'
        f'Trigger: {trigger}',
    )
    return True


def register_rep_handler(automation):
    
    
    
    
    @automation.main_client.on(events.NewMessage(outgoing=True))
    async def _rep(event):
        raw = re.sub(
            r"[\ufeff\u200b\u200c\u200d]",
            "",
            event.raw_text or "",
        ).strip()
        if REP_LIST_RE.fullmatch(raw):
            if automation.is_admin(event):
                await _list_rep_jobs(event)
            return
        stop_match = REP_STOP_RE.fullmatch(raw)
        if stop_match:
            if automation.is_admin(event):
                target_chat = (
                    int(stop_match.group("chat_id"))
                    if stop_match.group("chat_id") is not None
                    else event.chat_id
                )
                await _stop_rep_job(
                    event,
                    stop_match.group("trigger"),
                    target_chat,
                )
            return
        if not re.match(r"^(?:repall|rep|reploop)\d*\(", raw, re.IGNORECASE):
            return
        logger.info(
            "[REP-CMD] Command received from Main: chat_id=%s msg_id=%s",
            event.chat_id,
            event.id,
        )
        if not automation.is_admin(event):
            logger.warning(
                "[REP-CMD] Ignored command: sender_id=%s admin_id=%s",
                event.sender_id,
                automation._admin_user_id,
            )
            return
        parsed = parse_rep_command(raw)
        if not parsed:
            logger.warning("[REP-CMD] Parser rejected command: %r", event.raw_text)
            await _edit_status(
                event,
                '⚠️ Usage: `rep(m) [TRIGGER_SENDER_ID] "/catch" '
                'send "/wa"`\n'
                'Chain: `rep(1) [TRIGGER_SENDER_ID] "/catch" '
                'send "/wa" /5 sendrep [RESPONDER_ID] "LINE 1"`\n'
                'All accounts: `repall(1) [TRIGGER_SENDER_ID] "/catch" '
                'send "/wa"` (one trigger per account), or '
                '`repall24(1) ...` (24 triggers per account).\n'
                'Accounts: `rep25(random) ... send(m) "LINE 1"`; '
                'the first account is chosen per trigger and the LINE account '
                'is fixed for rep.\n'
                'Timing: `/5` = wait 5s after the first reply; default first-reply '
                'window is 10s. `/5-10` sets that window explicitly; `/5-0` = '
                'unlimited.\n'
                'IDs are optional; without them, any sender/reply is accepted.\n'
                'Stop here: `reploop "/catch"` or `rep stop "/catch"`\n'
                'Stop from another chat: add its chat ID at the end.'
            )
            return
        await start_rep_job(automation, parsed, event)


def register_rep_temp_command(automation, event):
    """Handle the same command from an active Temporary Main user."""
    parsed = parse_rep_command(event.raw_text)
    if not parsed:
        return False
    asyncio.create_task(start_rep_job(automation, parsed, event))
    return True

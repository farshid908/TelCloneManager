"""
Send command handler with state persistence, bridge fallback, and Dclick support.
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple

from telethon import TelegramClient, events

from config import BUTTON_CLICK_TIMEOUT, BRIDGE_GROUP
from command_parser import parse_send_command
from entity_resolver import ChatTarget, auto_extract_chat_target
from message_sender import safe_send_message, pre_bridge_target
from loop_manager import make_loop_key
from state_persistence import (
    register_loop,
    update_loop_iteration,
    unregister_loop,
    update_loop_progress,
    register_sequential,
    update_sequential_progress,
    unregister_sequential,
    get_all_loops,
)

logger = logging.getLogger("TG-Auto")

NO_TRACE_DELETE_DELAY = 5.0


async def _render_sender_text(client, text, identity_cache):
    """Replace the literal ID token with the sending account's Telegram ID."""
    if "ID" not in text:
        return text
    cache_key = id(client)
    sender_id = identity_cache.get(cache_key)
    if sender_id is None:
        me = await client.get_me()
        sender_id = getattr(me, "id", None)
        if sender_id is None:
            raise RuntimeError("Could not resolve the sending account ID")
        identity_cache[cache_key] = int(sender_id)
    return text.replace("ID", str(sender_id))


async def _auto_delete(client, chat_id, msg_ids, delay):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
        logger.info(f"[DELETE] 🗑️ Auto-deleted {len(msg_ids)} message(s)")
    except Exception as e:
        logger.warning(f"[DELETE] Failed: {e}")


async def _edit_status(message, text):
    """Edit the existing status message without interrupting the job."""
    if message is None:
        return
    try:
        await message.edit(text)
    except Exception as e:
        
        
        logger.debug(f"[SEND] Status edit skipped: {e}")


def _select_clones(all_clients, all_names, clone_indices):
    if clone_indices is None:
        return all_clients, all_names

    selected_clients = []
    selected_names = []
    skipped = []
    for idx in clone_indices:
        zero_idx = idx - 1
        if 0 <= zero_idx < len(all_clients):
            selected_clients.append(all_clients[zero_idx])
            selected_names.append(all_names[zero_idx])
        else:
            skipped.append(idx)
    if skipped:
        logger.warning(f"[SEND] Skipped invalid indices: {skipped}")
    return selected_clients, selected_names


def _build_flags_display(parsed):
    flags = ["+me" if parsed["include_me"] else "-me", f"/{parsed['delay']}"]
    if parsed["loop_interval"]:
        flags.append(f"loop{parsed['loop_interval']}")
    if parsed.get("rep_chat_id") is not None:
        flags.append(f"rep={parsed['rep_chat_id']}")
    if parsed.get("reply_timeout") is not None:
        flags.append(f"t{parsed['reply_timeout']}")
    if parsed["button_text"]:
        click_flag = f'click "{parsed["button_text"]}"'
        if parsed.get("click_chain_delay") is not None:
            click_flag = (
                f'click() /{parsed["click_chain_delay"]} '
                f'"{parsed["button_text"]}"'
            )
        flags.append(click_flag)
    if parsed.get("dclick_text"):
        flags.append(f'D{parsed["dclick_delay"]}click "{parsed["dclick_text"]}"')
    if parsed["reply_text"]:
        flags.append(f'reply "{parsed["reply_text"]}"')
    if parsed["if_structure"]:
        flags.append(f'if [{parsed["if_structure"]["type"]}]')
    if parsed["no_trace"]:
        flags.append("-n")
    return flags


def _build_if_display(if_struct):
    lines = [f"🔀 If    : `{if_struct['condition']}`"]
    if if_struct.get("type") == "nested" and if_struct.get("nested_command"):
        lines.append(f"  Nested: `{if_struct['nested_command']}`")
    for branch_name, branch_key in [("Then", "then"), ("Elif", "elif"), ("Else", "else")]:
        branch = if_struct.get(branch_key)
        if branch:
            acts = []
            if branch.get("click"):
                acts.append(f'click "{branch["click"]}"')
            if branch.get("reply"):
                acts.append(f'reply "{branch["reply"]}"')
            if acts:
                lines.append(f"  {branch_name}: {' + '.join(acts)}")
    return lines


def register_send_handler(automation):
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"""^send[\s(]""", from_users=aid,
    ))
    async def _send(event):
        if not automation.is_admin(event):
            return

        parsed = parse_send_command(event.raw_text)
        if not parsed:
            return

        chat_target = await auto_extract_chat_target(
            automation.main_client, event,
            automation.clone_clients, automation.clone_names,
            BRIDGE_GROUP,
        )

        text = parsed["text"]
        include_me = parsed["include_me"]
        delay = parsed["delay"]
        loop_interval = parsed["loop_interval"]
        rep_chat_id = parsed.get("rep_chat_id")
        reply_timeout = parsed.get("reply_timeout")
        button_text = parsed["button_text"]
        click_chain_delay = parsed.get("click_chain_delay")
        reply_text = parsed["reply_text"]
        no_trace = parsed["no_trace"]
        clone_indices = parsed["clone_indices"]
        if_structure = parsed["if_structure"]
        dclick_text = parsed.get("dclick_text")
        dclick_delay = parsed.get("dclick_delay", 0)
        
        
        reply_to = event.reply_to_msg_id if event.is_reply else None

        selected_clients, selected_names = _select_clones(
            automation.clone_clients,
            automation.clone_names,
            clone_indices,
        )

        flags = _build_flags_display(parsed)

        clone_display = (
            f"Selected: {clone_indices} ({len(selected_clients)} clone(s))"
            if clone_indices else
            f"All {len(selected_clients)} clone(s)"
        )

        reply_lines = [
            f"📤 **Send Command**",
            f"Target  : `{chat_target.send_target}`",
            f"Text    : `{text[:100]}`",
            f"Flags   : {' '.join(flags)}",
            f"Sessions: {'Main + ' if include_me else ''}{clone_display}",
        ]
        if reply_to:
            reply_lines.append("Reply   : enabled")

        if if_structure:
            reply_lines.extend(_build_if_display(if_structure))
        if loop_interval:
            reply_lines.append(
                f"🔁 Loop   : every {loop_interval}s "
                f"(stop with `stop \"{text}\"`)"
            )
        initial_status_text = "\n".join(reply_lines)

        
        
        edited_command = False
        try:
            reply_msg = await event.edit(initial_status_text)
            edited_command = True
        except Exception as e:
            logger.debug(f"[SEND] Could not edit command message: {e}")
            reply_msg = await event.reply(initial_status_text)

        if no_trace:
            
            
            msgs_to_delete = []
            if reply_msg:
                msgs_to_delete.append(reply_msg.id)
            if not edited_command and reply_msg and reply_msg.id != event.id:
                msgs_to_delete.append(event.id)
            asyncio.create_task(
                _auto_delete(
                    automation.main_client, event.chat_id,
                    msgs_to_delete, NO_TRACE_DELETE_DELAY,
                )
            )

        params = {
            "text": text,
            "include_me": include_me,
            "delay": delay,
            "loop_interval": loop_interval,
            "rep_chat_id": rep_chat_id,
            "reply_timeout": reply_timeout,
            "button_text": button_text,
            "click_chain_delay": click_chain_delay,
            "reply_text": reply_text,
            "no_trace": no_trace,
            "clone_indices": clone_indices,
            "if_structure": if_structure,
            "dclick_text": dclick_text,
            "dclick_delay": dclick_delay,
            "reply_to": reply_to,
            "sender_order": (
                (["Main"] if include_me else []) + list(selected_names)
            ),
        }

        if loop_interval is not None:
            register_loop(event.chat_id, text, params)

            key = make_loop_key(event.chat_id, text)
            task = asyncio.create_task(
                _send_loop(
                    automation, key, chat_target,
                    text, include_me, delay, loop_interval,
                    button_text=button_text,
                    click_chain_delay=click_chain_delay,
                    reply_text=reply_text,
                    if_structure=if_structure,
                    selected_clients=selected_clients,
                    selected_names=selected_names,
                    reply_to=reply_to,
                    dclick_text=dclick_text,
                    dclick_delay=dclick_delay,
                    status_message=reply_msg,
                    initial_status_text=initial_status_text,
                    rep_chat_id=rep_chat_id,
                    reply_timeout=reply_timeout,
                    loop_id=make_loop_key(event.chat_id, text),
                    sender_order=params["sender_order"],
                )
            )
            await automation.loops.register(key, task)
        else:
            await _execute_send_with_state(
                automation, chat_target, text,
                include_me, delay,
                reply_to=reply_to,
                button_text=button_text,
                click_chain_delay=click_chain_delay,
                reply_text=reply_text,
                if_structure=if_structure,
                selected_clients=selected_clients,
                selected_names=selected_names,
                dclick_text=dclick_text,
                dclick_delay=dclick_delay,
                status_message=reply_msg,
                initial_status_text=initial_status_text,
            )


async def _execute_send_with_state(
    automation,
    chat_target,
    text,
    include_me,
    delay,
    reply_to=None,
    button_text=None,
    click_chain_delay=None,
    reply_text=None,
    if_structure=None,
    selected_clients=None,
    selected_names=None,
    resume_from=None,
    seq_id=None,
    dclick_text=None,
    dclick_delay=0,
    status_message=None,
    initial_status_text=None,
    loop_id=None,
    start_sender_index=0,
    sender_order=None,
):
    clone_clients = (
        selected_clients if selected_clients is not None
        else automation.clone_clients
    )
    clone_names = (
        selected_names if selected_names is not None
        else automation.clone_names
    )

    senders = []
    if include_me:
        senders.append((automation.main_client, "Main"))
    for c, n in zip(clone_clients, clone_names):
        senders.append((c, n))

    if sender_order:
        by_name = {name: (client, name) for client, name in senders}
        ordered = [by_name[name] for name in sender_order if name in by_name]
        if ordered:
            senders = ordered

    if not senders:
        logger.warning("[SEND] No senders available")
        return

    if start_sender_index:
        if start_sender_index >= len(senders):
            start_sender_index = 0
        else:
            logger.info(
                "[SEND] Resuming from sender %s/%s (%s)",
                start_sender_index + 1, len(senders),
                senders[start_sender_index][1],
            )
            senders = senders[start_sender_index:]

    if resume_from:
        original_count = len(senders)
        senders = [(c, n) for c, n in senders if n not in resume_from]
        skipped = original_count - len(senders)
        if skipped > 0:
            logger.info(f"[SEND] Resuming — skipped {skipped} already-sent")

    if not senders:
        if seq_id:
            unregister_sequential(seq_id)
        return

    bridge_id = BRIDGE_GROUP
    identity_cache = {}

    
    effective_target = chat_target.send_target
    if clone_clients:
        try:
            effective_target = await pre_bridge_target(
                target=chat_target.send_target,
                main_client=automation.main_client,
                bridge_group_id=bridge_id,
                all_clone_clients=clone_clients,
                all_clone_names=clone_names,
            )
        except Exception as e:
            logger.error(f"[SEND] Pre-bridge failed: {e}")

    if delay == 0:
        async def _send_batch_one(c, n):
            rendered_text = await _render_sender_text(
                c, text, identity_cache
            )
            return await safe_send_message(
                c, chat_target, rendered_text, session_name=n,
                reply_to=reply_to,
                button_text=button_text,
                click_chain_delay=click_chain_delay,
                reply_text=reply_text,
                if_structure=if_structure,
                main_client=automation.main_client,
                bridge_group_id=bridge_id,
                all_clone_clients=automation.clone_clients,
                all_clone_names=automation.clone_names,
                override_target=effective_target,
                dclick_text=dclick_text,
                dclick_delay=dclick_delay,
            )

        tasks = [
            _send_batch_one(c, n)
            for c, n in senders
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ok = sum(1 for r in results if r is True)
        logger.info(
            f"[SEND] Batch: ✓{ok} ✗{len(results)-ok} "
            f"→ {effective_target!r}"
        )
        await _edit_status(status_message, initial_status_text)
        if seq_id:
            unregister_sequential(seq_id)
    else:
        params = {
            "text": text, "include_me": include_me, "delay": delay,
            "loop_interval": None, "button_text": button_text,
            "click_chain_delay": click_chain_delay,
            "reply_text": reply_text, "no_trace": False,
            "clone_indices": None, "if_structure": if_structure,
            "dclick_text": dclick_text, "dclick_delay": dclick_delay,
        }

        if seq_id is None:
            all_senders_count = len(senders) + len(resume_from or [])
            seq_id = register_sequential(
                chat_target.chat_id, text, params, all_senders_count,
            )
            for name in (resume_from or []):
                update_sequential_progress(seq_id, name, delay_after=0)

        try:
            async def _send_one(c, n):
                rendered_text = await _render_sender_text(
                    c, text, identity_cache
                )
                return await safe_send_message(
                    c, chat_target, rendered_text, session_name=n,
                    reply_to=reply_to,
                    button_text=button_text,
                    click_chain_delay=click_chain_delay,
                    reply_text=reply_text,
                    if_structure=if_structure,
                    main_client=automation.main_client,
                    bridge_group_id=bridge_id,
                    all_clone_clients=automation.clone_clients,
                    all_clone_names=automation.clone_names,
                    override_target=effective_target,
                    dclick_text=dclick_text,
                    dclick_delay=dclick_delay,
                    return_message=loop_id is not None,
                )

            
            
            
            
            if button_text and len(senders) > 1:
                pending = []
                for i, (c, n) in enumerate(senders):
                    task = asyncio.create_task(_send_one(c, n))
                    pending.append((n, task))
                    if i < len(senders) - 1:
                        await asyncio.sleep(delay)

                for n, task in pending:
                    try:
                        result = await task
                        if result is True:
                            update_sequential_progress(
                                seq_id, n, delay_after=0
                            )
                        else:
                            logger.warning(
                                "[SEND] [%s] Send/click operation failed",
                                n,
                            )
                    except Exception as exc:
                        logger.error(
                            "[SEND] [%s] Independent send task failed: %s",
                            n,
                            exc,
                        )
            else:
                for i, (c, n) in enumerate(senders):
                    absolute_index = start_sender_index + i
                    if loop_id is not None:
                        update_loop_progress(
                            loop_id[0], loop_id[1],
                            phase="sending",
                            current_sender_index=absolute_index,
                            current_sender_name=n,
                            next_sender_index=absolute_index,
                            last_action="starting_sender",
                        )
                    result = await _send_one(c, n)
                    is_last = (i == len(senders) - 1)
                    delay_after = 0 if is_last else delay
                    if result:
                        sent_id = getattr(result, "id", None)
                        if loop_id is not None:
                            update_loop_progress(
                                loop_id[0], loop_id[1],
                                phase="sender_completed",
                                next_sender_index=absolute_index + 1,
                                current_sender_index=absolute_index,
                                current_sender_name=n,
                                last_sent_message_id=sent_id,
                                last_action="sender_completed",
                            )
                        update_sequential_progress(
                            seq_id, n, delay_after=delay_after
                        )
                    elif loop_id is not None:
                        update_loop_progress(
                            loop_id[0], loop_id[1],
                            phase="sender_failed",
                            current_sender_index=absolute_index,
                            current_sender_name=n,
                            next_sender_index=absolute_index,
                            last_action="sender_failed",
                        )
                    if not is_last:
                        await asyncio.sleep(delay)
            await _edit_status(status_message, initial_status_text)
        finally:
            unregister_sequential(seq_id)


async def _send_loop(
    automation,
    key,
    chat_target,
    text,
    include_me,
    delay,
    loop_interval,
    button_text=None,
    click_chain_delay=None,
    reply_text=None,
    if_structure=None,
    selected_clients=None,
    selected_names=None,
    reply_to=None,
    initial_sleep=0,
    dclick_text=None,
    dclick_delay=0,
    status_message=None,
    initial_status_text=None,
    rep_chat_id=None,
    reply_timeout=None,
    loop_id=None,
    sender_order=None,
):
    chat_id = key[0]

    if initial_sleep > 0:
        logger.info(
            f"[LOOP] Resuming — waiting {initial_sleep:.0f}s"
        )
        try:
            await asyncio.sleep(initial_sleep)
        except asyncio.CancelledError:
            unregister_loop(chat_id, text)
            raise

    persisted = get_all_loops().get(f"{chat_id}:{text}", {})
    iteration = int(persisted.get("iteration", 0))
    start_sender_index = int(persisted.get("next_sender_index", 0) or 0)
    resume_current_cycle = persisted.get("phase") not in {
        None, "scheduled", "cycle_completed"
    }
    
    
    sleep_interval = max(1, int(loop_interval or 0))
    try:
        while True:
            if not resume_current_cycle:
                iteration += 1
            resume_current_cycle = False
            if loop_id is not None:
                update_loop_progress(
                    chat_id, text,
                    iteration=iteration,
                    phase="cycle_started",
                    next_sender_index=start_sender_index,
                    last_action="cycle_started",
                )
            logger.info(
                f"[LOOP] ━━━ Iteration {iteration} ━━━ "
                f'chat={chat_id} text="{text}"'
            )
            if rep_chat_id is not None:
                await _execute_reply_chain_loop(
                    automation, chat_target, text, include_me, delay,
                    loop_interval,
                    selected_clients=selected_clients,
                    selected_names=selected_names,
                    rep_chat_id=rep_chat_id,
                    button_text=button_text,
                    click_chain_delay=click_chain_delay,
                    reply_timeout=reply_timeout,
                )
            else:
                await _execute_send_with_state(
                    automation, chat_target, text, include_me, delay,
                    button_text=button_text,
                    click_chain_delay=click_chain_delay,
                    reply_text=reply_text,
                    if_structure=if_structure,
                    selected_clients=selected_clients,
                    selected_names=selected_names,
                    reply_to=reply_to,
                    dclick_text=dclick_text,
                    dclick_delay=dclick_delay,
                    status_message=status_message,
                    initial_status_text=initial_status_text,
                    loop_id=loop_id,
                    start_sender_index=start_sender_index,
                    sender_order=sender_order,
                )
            update_loop_iteration(chat_id, text)
            if loop_id is not None:
                update_loop_progress(
                    chat_id, text,
                    iteration=iteration,
                    phase="cycle_completed",
                    next_sender_index=0,
                    current_sender_index=None,
                    current_sender_name=None,
                    last_action="cycle_completed",
                )
            start_sender_index = 0
            await _edit_status(
                status_message,
                f"🔁 **Loop active**\n"
                f"Target: `{chat_target.send_target}`\n"
                f"Iteration: {iteration} ✅\n"
                f"Next run in: {sleep_interval}s",
            )

            if rep_chat_id is None:
                logger.info(f"[LOOP] Sleeping {sleep_interval}s…")
                await asyncio.sleep(sleep_interval)
            else:
                logger.info(
                    "[LOOP] Reply-chain timing is per sender; next due time "
                    "is managed by the chain scheduler"
                )
                
                
                
                
                logger.info(
                    f"[LOOP] Reply chain complete; sleeping {sleep_interval}s…"
                )
                await asyncio.sleep(sleep_interval)
    except asyncio.CancelledError:
        logger.info(
            f'[LOOP] 🛑 Stopped after {iteration} iteration(s) — "{text}"'
        )
    except Exception as e:
        logger.error(f"[LOOP] ✗ Error: {e}")
        
        
        
        unregister_loop(chat_id, text)
    finally:
        automation.loops.remove(key)






async def resume_all_loops(automation):
    from state_persistence import get_all_loops

    
    
    resume_lock = getattr(automation, "_loop_resume_lock", None)
    if resume_lock is None:
        resume_lock = asyncio.Lock()
        automation._loop_resume_lock = resume_lock
    if resume_lock.locked():
        logger.warning("[RESUME] Loop resume already in progress; skipping duplicate call")
        return

    async with resume_lock:
        loops = get_all_loops()
        if not loops:
            logger.info("[RESUME] No loops to resume")
            return

        logger.info(f"[RESUME] Resuming {len(loops)} loop(s)…")
        now = time.time()

        for loop_id, info in loops.items():
            try:
                chat_id = info["chat_id"]
                text = info["text"]
                params = info["params"]
                next_at = info.get("next_iteration_at", now)
                key = make_loop_key(chat_id, text)

                
                
                
                if automation.loops.is_active(key):
                    logger.warning(
                        f'[RESUME] Skipping duplicate active loop: '
                        f'chat={chat_id} text="{text}"'
                    )
                    continue

                time_until_next = next_at - now
                if time_until_next < 0:
                    logger.info(
                        f"[RESUME] Loop \"{text}\" overdue "
                        f"{-time_until_next:.0f}s — delaying one full interval"
                    )
                    
                    
                    
                    
                    initial_sleep = max(0, params.get("loop_interval", 0))
                else:
                    initial_sleep = time_until_next
                    logger.info(
                        f"[RESUME] Loop \"{text}\" — waiting "
                        f"{initial_sleep:.0f}s"
                    )

                chat_target = await _get_chat_target_by_id(automation, chat_id)
                if chat_target is None:
                    continue

                selected_clients, selected_names = _select_clones(
                    automation.clone_clients,
                    automation.clone_names,
                    params.get("clone_indices"),
                )

                task = asyncio.create_task(
                    _send_loop(
                        automation, key, chat_target,
                        text,
                        params["include_me"],
                        params["delay"],
                        params["loop_interval"],
                        reply_text=params.get("reply_text"),
                        click_chain_delay=params.get("click_chain_delay"),
                        if_structure=params.get("if_structure"),
                        selected_clients=selected_clients,
                        selected_names=selected_names,
                        reply_to=params.get("reply_to"),
                        initial_sleep=initial_sleep,
                        dclick_text=params.get("dclick_text"),
                        dclick_delay=params.get("dclick_delay", 0),
                        rep_chat_id=params.get("rep_chat_id"),
                        button_text=params.get("button_text"),
                        reply_timeout=params.get("reply_timeout"),
                        loop_id=key,
                        sender_order=params.get("sender_order"),
                    )
                )
                await automation.loops.register(key, task)
                logger.info(f"[RESUME] ✓ Resumed loop: {loop_id}")

            except Exception as e:
                logger.error(f"[RESUME] ✗ Failed to resume {loop_id}: {e}")


async def _execute_reply_chain_loop(
    automation,
    chat_target,
    text,
    include_me,
    delay,
    loop_interval,
    selected_clients=None,
    selected_names=None,
    rep_chat_id=None,
    button_text=None,
    click_chain_delay=None,
    reply_timeout=None,
):
    """Send one reply-triggered chain iteration.

    Each sender waits for the designated Telegram account to reply to the
    exact message just sent before the next sender is allowed to start.
    ``delay`` is applied after that reply and before the next sender.
    """
    if rep_chat_id != chat_target.chat_id:
        logger.warning(
            "[SEND] rep=%s does not match target chat %s; using target chat",
            rep_chat_id, chat_target.chat_id,
        )

    clients = selected_clients if selected_clients is not None else automation.clone_clients
    names = selected_names if selected_names is not None else automation.clone_names
    senders = []
    if include_me:
        senders.append((automation.main_client, "Main"))
    senders.extend(zip(clients, names))
    if not senders:
        return

    effective_target = chat_target.send_target
    identity_cache = {}
    if clients:
        try:
            effective_target = await pre_bridge_target(
                target=effective_target,
                main_client=automation.main_client,
                bridge_group_id=BRIDGE_GROUP,
                all_clone_clients=clients,
                all_clone_names=names,
            )
        except Exception as exc:
            logger.error("[SEND] Reply-chain bridge failed: %s", exc)

    for index, (client, name) in enumerate(senders):
        
        
        
        reply_event = asyncio.Event()
        pending_reply_ids = []
        reply_handler = _make_designated_reply_handler(
            reply_event, pending_reply_ids, chat_target.chat_id,
            rep_chat_id, name,
        )
        client.add_event_handler(
            reply_handler, events.NewMessage(chats=chat_target.chat_id),
        )
        try:
            rendered_text = await _render_sender_text(
                client, text, identity_cache
            )
            sent = await safe_send_message(
                client, chat_target, rendered_text, session_name=name,
                button_text=button_text,
                click_chain_delay=click_chain_delay,
                main_client=automation.main_client,
                bridge_group_id=BRIDGE_GROUP,
                all_clone_clients=automation.clone_clients,
                all_clone_names=names,
                override_target=effective_target,
                return_message=True,
            )
            if sent is False or sent is None:
                logger.warning(
                    "[REP] [%s] Message was not sent; stopping this chain",
                    name,
                )
                return

            
            
            reply_handler.reply_to_id = sent.id
            if sent.id in pending_reply_ids:
                reply_event.set()
            logger.info(
                '[REP] [%s] Waiting for sender %s to reply to msg_id=%s',
                name, rep_chat_id, sent.id,
            )
            if reply_timeout is None:
                await reply_event.wait()
            else:
                try:
                    await asyncio.wait_for(reply_event.wait(), reply_timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[REP] [%s] No trigger reply within %ss; continuing",
                        name, reply_timeout,
                    )
        finally:
            client.remove_event_handler(reply_handler)

        if index < len(senders) - 1 and delay > 0:
            logger.info("[REP] Waiting %ss before the next sender", delay)
            await asyncio.sleep(delay)


def _make_designated_reply_handler(
    matched, pending_reply_ids, chat_id, sender_id, session_name,
):
    """Build a handler that accepts only the configured exact reply."""
    async def _on_message(event):
        try:
            event_sender = event.sender_id
            event_reply_to = event.reply_to_msg_id
            if (
                event_sender == sender_id
                and event_reply_to is not None
            ):
                pending_reply_ids.append(event_reply_to)
                expected_id = getattr(_on_message, "reply_to_id", None)
                if expected_id is None or event_reply_to != expected_id:
                    return
                logger.info(
                    "[REP] [%s] Received valid trigger reply from %s to msg_id=%s",
                    session_name, sender_id, event_reply_to,
                )
                matched.set()
        except Exception:
            logger.debug("[REP] Reply event inspection failed", exc_info=True)

    _on_message.reply_to_id = None
    return _on_message


async def resume_all_sequentials(automation):
    from state_persistence import get_all_sequentials

    sequentials = get_all_sequentials()
    if not sequentials:
        logger.info("[RESUME] No sequential sends to resume")
        return

    
    
    
    
    
    stale_count = len(sequentials)
    for seq_id in sequentials:
        unregister_sequential(seq_id)
    logger.warning(
        f"[RESUME] Discarded {stale_count} unfinished sequential send(s); "
        "one-shot sends are not resumed after restart"
    )
    return


async def _get_chat_target_by_id(automation, chat_id):
    from entity_resolver import ChatTarget
    try:
        entity = await automation.main_client.get_entity(chat_id)
        return ChatTarget(
            chat_id=chat_id,
            username=(
                f"@{entity.username}"
                if getattr(entity, "username", None)
                else None
            ),
            entity_type="unknown",
            display_name=(
                getattr(entity, "title", None) or str(chat_id)
            ),
            send_target=(
                f"@{entity.username}"
                if getattr(entity, "username", None)
                else chat_id
            ),
        )
    except Exception as e:
        logger.error(
            f"[RESUME] Can't get chat entity for {chat_id}: {e}"
        )
        return None

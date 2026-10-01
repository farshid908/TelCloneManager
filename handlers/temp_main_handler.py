"""
Handler for temporary main commands:
  temp main    — reply to a user to make them temp main
  untmp main   — remove temp main, restore original

When temp main is active, this handler catches ALL messages from
the temp main user and processes known commands:
  - send, stop, stopall
  - mirror on/off + mirror replication (text + media)
  - react
  - click
  - /go, /back, /status
  - +me is always disabled for temp main
"""

import asyncio
import logging
import random
import re

from telethon import events
from telethon.tl.types import User, Channel
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest

from temp_main_manager import temp_main

logger = logging.getLogger("TG-Auto")

_temp_handlers = []
_temp_go_seen = set()
_temp_go_lock = asyncio.Lock()

TG_LINK_RE = re.compile(
    r'https?://t\.me/(?:c/(?P<chat_id>\d+)|(?P<username>\w+))/(?P<msg_id>\d+)'
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _auto_delete(event, delay=2.0):
    """Auto-delete event message after delay."""
    await asyncio.sleep(delay)
    try:
        await event.delete()
    except Exception:
        pass


async def _auto_delete_msgs(client, chat_id, msg_ids, delay=2.0):
    """Wait then delete specific messages."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
    except Exception:
        pass


async def _edit_and_delete(event, text, delay=10.0):
    """Edit the command message, then remove it quietly."""
    try:
        await event.edit(text, parse_mode=None)
    except Exception:
        logger.debug("[TEMP-MAIN] Could not edit command message", exc_info=True)
    asyncio.create_task(_auto_delete(event, delay))


async def _stop_loops_preserve_state(automation):
    """Stop live send loops but keep their persisted definitions intact."""
    try:
        count = await automation.loops.cancel_all()
        logger.info(
            "[TEMP-MAIN] Stopped %s loop(s), keeping state for resume",
            count,
        )
        return count
    except Exception:
        logger.exception("[TEMP-MAIN] Failed to stop loops")
        return 0


async def _resolve_user(client, raw):
    """Resolve a user ID or username and require a real Telegram user."""
    value = str(raw).strip()
    try:
        entity = await client.get_entity(int(value)) if value.lstrip("-").isdigit() \
            else await client.get_entity(value.lstrip("@"))
    except Exception:
        return None
    return entity if isinstance(entity, User) else None


async def _resolve_target(client, raw):
    """Resolve a target chat ID or username to an entity ID and label."""
    value = str(raw).strip()
    try:
        if value.lstrip("-").isdigit():
            entity = await client.get_entity(int(value))
        else:
            entity = await client.get_entity(value.lstrip("@"))
    except Exception:
        return None, None
    return getattr(entity, "id", None), value


def _temp_event_allowed(event):
    """Apply the optional target-chat scope of Temporary Main."""
    target = temp_main.target_chat_id
    return target is None or event.chat_id == target


async def _cancel_all_runtime_jobs(automation):
    """Stop and forget every resumable automation job."""
    cancelled = 0
    try:
        cancelled = await automation.loops.cancel_all()
    except Exception:
        logger.exception("[TEMP-MAIN] Failed to cancel send loops")

    from state_persistence import (
        get_all_loops,
        get_all_sequentials,
        get_all_hunt_clicks,
        unregister_loop,
        unregister_sequential,
        unregister_hunt_click,
    )
    for info in get_all_loops().values():
        unregister_loop(info["chat_id"], info["text"])
    for seq_id in get_all_sequentials():
        unregister_sequential(seq_id)
    for task_id in get_all_hunt_clicks():
        unregister_hunt_click(task_id)

    from handlers.dclick_handler import cancel_all_dclick_jobs
    from handlers.click_handler import cancel_all_hunt_clicks
    from handlers.forward_handler import cancel_all_forwards
    from handlers.go_handler import cancel_all_join_jobs
    from handlers.rep_handler import cancel_all_rep_jobs
    await asyncio.gather(
        cancel_all_dclick_jobs(),
        cancel_all_hunt_clicks(),
        cancel_all_forwards(),
        cancel_all_join_jobs(),
        cancel_all_rep_jobs(),
        return_exceptions=True,
    )
    logger.info("[TEMP-MAIN] Stopped and removed %s send loop(s) and all jobs", cancelled)
    return cancelled


async def _handle_temp_go(automation, event):
    """Handle /go emitted by Temp Main in any chat seen by a client."""
    if not _temp_event_allowed(event):
        return
    from handlers.go_handler import parse_go_request, start_temp_main_go

    reply_text = None
    if event.is_reply:
        try:
            reply = await event.get_reply_message()
            reply_text = getattr(reply, "raw_text", None)
        except Exception:
            reply_text = None
    request = parse_go_request(event.raw_text, reply_text)
    if not request:
        logger.warning("[TEMP-MAIN] Invalid /go request: %s", event.raw_text)
        return

    # The same message can be observed by several clones. Start one job and
    # let that job operate all clones, instead of launching duplicates.
    key = (event.chat_id, event.id)
    async with _temp_go_lock:
        if key in _temp_go_seen:
            return
        _temp_go_seen.add(key)
        if len(_temp_go_seen) > 2000:
            _temp_go_seen.clear()
            _temp_go_seen.add(key)
        target, target_text = request
        await start_temp_main_go(
            automation,
            target,
            target_text,
            [temp_main.temp_user_id, temp_main.original_admin_id],
        )


def _build_clone_sessions(automation, clone_selector=None):
    """Build list of (client, name) for clones only (never Main)."""
    sessions = []

    if clone_selector is None or (
        isinstance(clone_selector, str)
        and clone_selector.lower() == "all"
    ):
        for c, n in zip(automation.clone_clients, automation.clone_names):
            sessions.append((c, n))
        return sessions

    if isinstance(clone_selector, str):
        from command_parser import parse_clone_selector
        indices = parse_clone_selector(clone_selector)
    else:
        indices = clone_selector

    if indices:
        for idx in indices:
            zi = idx - 1
            if 0 <= zi < len(automation.clone_clients):
                sessions.append((
                    automation.clone_clients[zi],
                    automation.clone_names[zi],
                ))

    return sessions


async def _resolve_link_ids(client, link_text):
    """Resolve telegram message link to (chat_id, msg_id)."""
    clean = link_text.strip().strip("[]<>()")
    m = TG_LINK_RE.search(clean)
    if not m:
        return None, None

    msg_id = int(m.group("msg_id"))

    if m.group("chat_id"):
        raw_id = int(m.group("chat_id"))
        for cid in [raw_id, int(f"-100{raw_id}")]:
            try:
                await client.get_entity(cid)
                return cid, msg_id
            except Exception:
                continue

    elif m.group("username"):
        try:
            entity = await client.get_entity(m.group("username"))
            return entity.id, msg_id
        except Exception:
            pass

    return None, None


async def _resolve_link_message(client, link_text):
    """Resolve telegram message link to (message, chat_id)."""
    clean = link_text.strip().strip("[]<>()")
    m = TG_LINK_RE.search(clean)
    if not m:
        return None, None

    msg_id = int(m.group("msg_id"))
    entity = None

    if m.group("chat_id"):
        raw_id = int(m.group("chat_id"))
        for cid in [raw_id, int(f"-100{raw_id}")]:
            try:
                entity = await client.get_entity(cid)
                break
            except Exception:
                continue

    elif m.group("username"):
        try:
            entity = await client.get_entity(m.group("username"))
        except Exception:
            pass

    if entity is None:
        return None, None

    try:
        message = await client.get_messages(entity, ids=msg_id)
        if message:
            return message, entity.id
    except Exception:
        pass

    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# Register handlers
# ─────────────────────────────────────────────────────────────────────────────

def register_temp_main_handler(automation):
    """Register temp main / untmp main handlers."""
    aid = automation._admin_user_id

    # ─── temp main (reply to user) ──────────────────────────────
    @automation.main_client.on(events.NewMessage(
        pattern=r'^temp\s+main(?:\s+.*)?$',
        from_users=aid,
    ))
    async def _temp_main_cmd(event):
        try:
            if event.sender_id != aid:
                return

            if temp_main.active:
                await _edit_and_delete(
                    event,
                    f"Temp Main is already active: {temp_main.temp_first_name}. "
                    "Use `untmp main` first.",
                )
                return

            parts = event.raw_text.strip().split()
            if len(parts) < 2 or parts[0].casefold() != "temp" \
                    or parts[1].casefold() != "main":
                await _edit_and_delete(
                    event,
                    'Usage: reply with `temp main`, or use '
                    '`temp main USER TARGET`.',
                )
                return

            args = parts[2:]
            stop_loops = False
            if args and args[0].casefold() == "-s":
                stop_loops = True
                args = args[1:]

            user = None
            target_chat_id = None
            target_label = None
            if not args:
                if not event.is_reply:
                    await _edit_and_delete(
                        event,
                        "Reply to a user with `temp main` or provide "
                        "`USER TARGET`.",
                    )
                    return
                reply_msg = await event.get_reply_message()
                if not reply_msg or not reply_msg.sender_id:
                    await _edit_and_delete(event, "Cannot identify the user.")
                    return
                user = await _resolve_user(
                    automation.main_client, str(reply_msg.sender_id)
                )
            elif len(args) == 2:
                user = await _resolve_user(automation.main_client, args[0])
                target_chat_id, target_label = await _resolve_target(
                    automation.main_client, args[1]
                )
            else:
                await _edit_and_delete(
                    event,
                    "Usage: temp main [-s] USER TARGET",
                )
                return

            if user is None:
                await _edit_and_delete(event, "The USER is not a valid user.")
                return
            if target_chat_id is None and len(args) == 2:
                await _edit_and_delete(event, "The TARGET is not accessible.")
                return
            if user.id == aid:
                await _edit_and_delete(event, "You cannot make yourself Temp Main.")
                return

            user_id = user.id
            username = user.username or str(user.id)
            first_name = user.first_name or "Unknown"
            stopped_count = (
                await _stop_loops_preserve_state(automation)
                if stop_loops else 0
            )

            temp_main.activate(
                user_id=user_id,
                username=username,
                first_name=first_name,
                original_admin_id=aid,
                stop_loops=stop_loops,
                target_chat_id=target_chat_id,
                target_label=target_label,
            )
            automation._temp_main_active = True
            automation._temp_main_user_id = user_id

            _register_temp_main_listeners(automation, user_id)

            scope = f" in {target_label}" if target_label else ""
            mode = "Loops stopped and will resume after untmp." \
                if stop_loops else "Existing loops continue running."
            await _edit_and_delete(
                event,
                f"Temporary Main activated for {first_name}{scope}.\n{mode}",
            )

            logger.info(
                f"[TEMP-MAIN] ✓ Activated: {first_name} "
                f"(@{username}), stop_loops={stop_loops}, "
                f"target={target_chat_id}, stopped={stopped_count}"
            )

        except Exception as e:
            logger.error(
                f"[TEMP-MAIN] Error: {type(e).__name__}: {e}",
                exc_info=True,
            )

    # ─── untmp main ─────────────────────────────────────────────
    @automation.main_client.on(events.NewMessage(
        pattern=r'^untmp\s+main\s*$',
        from_users=aid,
    ))
    async def _untmp_main_cmd(event):
        try:
            if event.sender_id != aid:
                return

            if not temp_main.active:
                await event.delete()
                return

            old_name = temp_main.temp_first_name
            old_username = temp_main.temp_username
            resume_loops = temp_main.stop_loops

            # The command is silent: remove it and revoke access immediately.
            try:
                await event.delete()
            except Exception:
                logger.debug("[UNTMP-MAIN] Could not delete command", exc_info=True)

            if resume_loops:
                await _stop_loops_preserve_state(automation)

            temp_main.deactivate()
            automation._temp_main_active = False
            automation._temp_main_user_id = None

            _remove_temp_main_listeners(automation)

            if resume_loops:
                from handlers.send_handler import resume_all_loops
                await resume_all_loops(automation)

            logger.info(
                f"[TEMP-MAIN] ✓ Deactivated: {old_name} (@{old_username})"
            )

        except Exception as e:
            logger.error(
                f"[UNTMP-MAIN] Error: {type(e).__name__}: {e}",
                exc_info=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Temp Main command dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def _register_temp_main_listeners(automation, temp_user_id):
    """Register event handler that processes temp main's commands."""
    global _temp_handlers

    logger.info(f"[TEMP-MAIN] Registering listeners for user {temp_user_id}")

    async def _temp_main_handler(event):
        try:
            if event.sender_id != temp_user_id:
                return
            if not temp_main.active:
                return
            if not _temp_event_allowed(event):
                return

            raw = (event.raw_text or "").strip()

            # Handle media-only messages (mirror)
            if not raw:
                if automation.mirror_mode and event.message.media:
                    await _cmd_mirror_media(automation, event)
                return

            raw_lower = raw.lower()

            if re.match(r"^/go(?:@\w+)?(?:\s|$)", raw_lower):
                await _handle_temp_go(automation, event)
                return

            # Mirror on/off
            if raw_lower == "mirror on":
                automation.mirror_mode = True
                reply = await event.reply("✅ **mirror mod on**")
                logger.info("[TEMP-MAIN] Mirror ON")
                asyncio.create_task(_auto_delete_msgs(
                    automation.main_client, event.chat_id,
                    [event.id, reply.id], 1.0,
                ))
                return

            if raw_lower == "mirror off":
                automation.mirror_mode = False
                reply = await event.reply("❌ **mirror mod off**")
                logger.info("[TEMP-MAIN] Mirror OFF")
                asyncio.create_task(_auto_delete_msgs(
                    automation.main_client, event.chat_id,
                    [event.id, reply.id], 1.0,
                ))
                return

            cmd_prefixes = (
                "/go", "/back", "send ", "mirror ",
                "stop", "/status", "click", "react",
                "rep(", "reploop",
                "temp ", "untmp",
                "save ", "del ", "list ", "set ",
                "show ", "hide ", "sync ", "prof ",
                "mainclick", "clone",
            )
            is_command = any(raw_lower.startswith(p) for p in cmd_prefixes)

            if automation.mirror_mode and not is_command:
                await _cmd_mirror_text(automation, event, raw)
                return

            if raw_lower.startswith("send"):
                await _cmd_send(automation, event, raw)
                return

            if raw_lower.startswith("stop"):
                await _cmd_stop(automation, event, raw, raw_lower)
                return

            if raw_lower.startswith("react"):
                await _cmd_react(automation, event, raw)
                return

            if raw_lower.startswith("rep(") or raw_lower.startswith("reploop"):
                from handlers.rep_handler import register_rep_temp_command
                if register_rep_temp_command(automation, event):
                    return
                await event.reply(
                    '⚠️ Usage: `rep(main) CHATID "/catch" send "/wa"`\n'
                    'Chain: `rep(clone1) CHATID "/catch" send "/wa" /5 '
                    'send CHATID "LINE 1"`'
                )
                return

            if raw_lower.startswith("click"):
                await _cmd_click(automation, event, raw)
                return

            if raw_lower == "/back":
                await _cmd_back(automation, event)
                return

            if raw_lower == "/status":
                await _cmd_status(automation, event)
                return

            if automation.mirror_mode:
                await _cmd_mirror_text(automation, event, raw)

        except Exception as e:
            logger.error(
                f"[TEMP-MAIN-LISTENER] Error: {type(e).__name__}: {e}",
                exc_info=True,
            )

    automation.main_client.add_event_handler(
        _temp_main_handler,
        events.NewMessage(from_users=temp_user_id),
    )

    # A Temp Main command can be sent in any group or private chat visible to
    # a clone. Register a lightweight /go listener on every clone as well.
    for clone_client in automation.clone_clients:
        async def _clone_go_handler(event, _automation=automation):
            if event.sender_id != temp_user_id or not temp_main.active:
                return
            if not _temp_event_allowed(event):
                return
            if re.match(r"^/go(?:@\w+)?(?:\s|$)", (event.raw_text or "").strip(), re.I):
                await _handle_temp_go(_automation, event)

        clone_client.add_event_handler(
            _clone_go_handler,
            events.NewMessage(from_users=temp_user_id),
        )
        _temp_handlers.append((clone_client, _clone_go_handler))

    _temp_handlers.append((automation.main_client, _temp_main_handler))
    logger.info("[TEMP-MAIN] Listeners registered")


def _remove_temp_main_listeners(automation):
    """Remove all temp main event handlers."""
    global _temp_handlers
    for client, handler in _temp_handlers:
        try:
            client.remove_event_handler(handler)
        except Exception:
            pass
    _temp_handlers.clear()
    logger.info("[TEMP-MAIN] Listeners removed")


# ─────────────────────────────────────────────────────────────────────────────
# Individual command implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _cmd_mirror_text(automation, event, text):
    """Mirror text message from temp main."""
    from entity_resolver import auto_extract_chat_target
    from message_sender import safe_send_message
    from config import BRIDGE_GROUP

    chat_target = await auto_extract_chat_target(
        automation.main_client, event,
        automation.clone_clients, automation.clone_names,
        BRIDGE_GROUP,
    )

    tasks = [
        safe_send_message(
            c, chat_target, text,
            session_name=n,
            reply_to=event.reply_to_msg_id,
        )
        for c, n in zip(automation.clone_clients, automation.clone_names)
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _cmd_mirror_media(automation, event):
    """Mirror media from temp main."""
    from entity_resolver import auto_extract_chat_target, resolve_entity
    from config import BRIDGE_GROUP

    chat_target = await auto_extract_chat_target(
        automation.main_client, event,
        automation.clone_clients, automation.clone_names,
        BRIDGE_GROUP,
    )

    try:
        media_bytes = await event.message.download_media(file=bytes)
        if not media_bytes:
            return
    except Exception as e:
        logger.error(f"[TEMP-MIRROR] Download failed: {e}")
        return

    caption = event.message.text or ""
    kwargs = {}
    if event.reply_to_msg_id:
        kwargs["reply_to"] = event.reply_to_msg_id
    if event.message.voice:
        kwargs["voice_note"] = True
    if event.message.video_note:
        kwargs["video_note"] = True

    for c, n in zip(automation.clone_clients, automation.clone_names):
        try:
            entity = await resolve_entity(
                c, chat_target.send_target, n, silent=True,
            )
            if entity:
                await c.send_file(
                    entity, media_bytes, caption=caption, **kwargs,
                )
        except Exception as e:
            logger.error(f"[TEMP-MIRROR] [{n}] Error: {e}")


async def _cmd_send(automation, event, raw):
    """Handle send command (force -me)."""
    from command_parser import parse_send_command
    from entity_resolver import auto_extract_chat_target
    from handlers.send_handler import _execute_send_with_state, _select_clones
    from config import BRIDGE_GROUP

    modified = re.sub(r'\+me\b', '-me', raw, flags=re.IGNORECASE)
    if '-me' not in modified.lower():
        modified = modified + " -me"

    parsed = parse_send_command(modified)
    if not parsed:
        return

    parsed["include_me"] = False

    chat_target = await auto_extract_chat_target(
        automation.main_client, event,
        automation.clone_clients, automation.clone_names,
        BRIDGE_GROUP,
    )

    selected_clients, selected_names = _select_clones(
        automation.clone_clients,
        automation.clone_names,
        parsed.get("clone_indices"),
    )

    if parsed.get("loop_interval") is not None:
        from loop_manager import make_loop_key
        from state_persistence import register_loop
        from handlers.send_handler import _send_loop

        register_loop(event.chat_id, parsed["text"], {
            **parsed, "include_me": False,
        })

        key = make_loop_key(event.chat_id, parsed["text"])
        task = asyncio.create_task(
            _send_loop(
                automation, key, chat_target,
                parsed["text"], False, parsed["delay"],
                parsed["loop_interval"],
                button_text=parsed.get("button_text"),
                reply_text=parsed.get("reply_text"),
                if_structure=parsed.get("if_structure"),
                selected_clients=selected_clients,
                selected_names=selected_names,
                dclick_text=parsed.get("dclick_text"),
                dclick_delay=parsed.get("dclick_delay", 0),
            )
        )
        await automation.loops.register(key, task)
        await event.reply(
            f"🔁 Loop started: `{parsed['text']}` "
            f"every {parsed['loop_interval']}s"
        )
    else:
        await _execute_send_with_state(
            automation, chat_target,
            parsed["text"], False, parsed["delay"],
            button_text=parsed.get("button_text"),
            reply_text=parsed.get("reply_text"),
            if_structure=parsed.get("if_structure"),
            selected_clients=selected_clients,
            selected_names=selected_names,
            dclick_text=parsed.get("dclick_text"),
            dclick_delay=parsed.get("dclick_delay", 0),
        )


async def _cmd_stop(automation, event, raw, raw_lower):
    """Handle stop / stopall."""
    from command_parser import parse_stop_command
    from loop_manager import make_loop_key
    from state_persistence import unregister_loop

    if raw_lower == "stopall":
        all_keys = list(automation.loops.active.keys())
        count = await automation.loops.cancel_all()
        for chat_id, text in all_keys:
            try:
                unregister_loop(chat_id, text)
            except Exception:
                pass
        await event.reply(f"🛑 Stopped {count} loop(s)")
        return

    stop_text = parse_stop_command(raw)
    if stop_text:
        key = make_loop_key(event.chat_id, stop_text)
        await automation.loops.cancel_by_key(key)
        try:
            unregister_loop(event.chat_id, stop_text)
        except Exception:
            pass
        await event.reply(f'🛑 Stopped loop for "{stop_text}"')


async def _cmd_react(automation, event, raw):
    """Handle react command."""
    from command_parser import parse_react_with_link_command
    from react_engine import (
        ALL_REACTIONS,
        NEGATIVE_REACTIONS,
        POSITIVE_REACTIONS,
        resolve_peer_and_react,
    )
    from config import BRIDGE_GROUP

    parsed = parse_react_with_link_command(raw)
    if not parsed:
        return

    emoji = parsed["emoji"]
    clone_indices = parsed["clone_indices"]
    links = parsed.get("links") or []
    targets = []

    if links:
        for link in links:
            target = await _resolve_link_ids(
                automation.main_client, link,
            )
            if target[0] is not None and target[1] is not None:
                targets.append(target)
    elif event.is_reply:
        reply = await event.get_reply_message()
        if reply:
            targets = [(event.chat_id, reply.id)]

    if not targets:
        return

    sessions = _build_clone_sessions(automation, clone_indices)

    pools = {
        "random": ALL_REACTIONS,
        "random+": POSITIVE_REACTIONS,
        "random-": NEGATIVE_REACTIONS,
    }
    random_mode = emoji.strip().lower()
    if random_mode in ("random-+", "random+-"):
        random_mode = "random"

    tasks = []
    random_used = set()
    for chat_id, msg_id in targets:
        for c, n in sessions:
            selected_emoji = emoji
            if random_mode in pools:
                available = [
                    item for item in pools[random_mode]
                    if item not in random_used
                ]
                if not available:
                    random_used.clear()
                    available = list(pools[random_mode])
                selected_emoji = random.choice(available)
                random_used.add(selected_emoji)
            tasks.append(resolve_peer_and_react(
                client=c, chat_id=chat_id, msg_id=msg_id, emoji=selected_emoji,
                session_name=n, main_client=automation.main_client,
                bridge_group_id=BRIDGE_GROUP,
                all_clone_clients=automation.clone_clients,
                all_clone_names=automation.clone_names,
            ))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    ok = sum(1 for r in results if r is True)
    logger.info(f"[TEMP-MAIN] React: ✓{ok} ✗{len(results)-ok}")
    asyncio.create_task(_auto_delete(event, 2.0))


async def _cmd_click(automation, event, raw):
    """Handle click command."""
    from click_engine import find_matching_button

    btn_match = re.match(
        r"""^click(?:\((?P<clones>[^)]+)\))?\s+(?P<q>["'])(?P<button>.+?)(?P=q)(?:\s+(?P<rest>.+))?\s*$""",
        raw,
        re.IGNORECASE | re.DOTALL,
    )

    if not btn_match:
        return

    button_text = btn_match.group("button").strip()
    rest = (btn_match.group("rest") or "").strip()
    clones_raw = btn_match.group("clones")

    clean_rest = re.sub(r'-[mn]\b', '', rest, flags=re.IGNORECASE).strip()
    clean_rest = clean_rest.strip("[]<>()")

    target_msg = None
    target_chat_id = None

    if clean_rest and TG_LINK_RE.search(clean_rest):
        target_msg, target_chat_id = await _resolve_link_message(
            automation.main_client, clean_rest,
        )
    elif event.is_reply:
        target_msg = await event.get_reply_message()
        if target_msg:
            target_chat_id = event.chat_id

    if target_msg is None or not target_msg.buttons:
        asyncio.create_task(_auto_delete(event, 2.0))
        return

    if clones_raw:
        sessions = _build_clone_sessions(automation, clones_raw)
        for client, name in sessions:
            try:
                try:
                    ent = await client.get_entity(target_chat_id)
                except Exception:
                    try:
                        ent = await client.get_entity(
                            int(f"-100{target_chat_id}")
                        )
                    except Exception:
                        continue

                msg = await client.get_messages(ent, ids=target_msg.id)
                if msg and msg.buttons:
                    match = find_matching_button(msg, button_text)
                    if match:
                        row_idx, col_idx, btn, _, _ = match
                        await msg.click(row_idx, col_idx)
                        logger.info(
                            f"[TEMP-MAIN] [{name}] ✓ Clicked \"{btn.text}\""
                        )
            except Exception as e:
                logger.error(f"[TEMP-MAIN] [{name}] Click failed: {e}")
            await asyncio.sleep(0.3)
    else:
        match = find_matching_button(target_msg, button_text)
        if match:
            row_idx, col_idx, btn, _, _ = match
            try:
                await target_msg.click(row_idx, col_idx)
                logger.info(
                    f"[TEMP-MAIN] ✓ Clicked \"{btn.text}\" "
                    f"at [{chr(65+row_idx)}{col_idx+1}]"
                )
            except Exception as e:
                logger.error(f"[TEMP-MAIN] Click failed: {e}")

    asyncio.create_task(_auto_delete(event, 2.0))


async def _cmd_go(automation, event):
    """Handle /go command."""
    from command_parser import extract_join_target
    from entity_resolver import resolve_entity

    reply_msg = await event.get_reply_message()
    if not reply_msg or not reply_msg.text:
        return

    target = extract_join_target(reply_msg.text)
    if not target:
        return

    label = target.get("hash") or target.get("username", "?")
    await event.reply(
        f"🚀 Joining **{label}** with "
        f"{len(automation.clone_clients)} clone(s)…"
    )

    results = {"success": 0, "failed": 0, "already": 0}

    for client, name in zip(
        automation.clone_clients, automation.clone_names
    ):
        try:
            if target["type"] == "invite":
                try:
                    await client(ImportChatInviteRequest(hash=target["hash"]))
                    results["success"] += 1
                except Exception:
                    results["already"] += 1
            else:
                entity = await resolve_entity(
                    client, target["username"], name
                )
                if entity:
                    try:
                        await client(JoinChannelRequest(entity))
                        results["success"] += 1
                    except Exception:
                        results["already"] += 1
                else:
                    results["failed"] += 1
        except Exception:
            results["failed"] += 1
        await asyncio.sleep(2)

    await event.reply(
        f"✅ **{label}**: ✓{results['success']} "
        f"⏭{results['already']} ✗{results['failed']}"
    )


async def _cmd_back(automation, event):
    """Handle /back command."""
    for client, name in zip(
        automation.clone_clients, automation.clone_names
    ):
        try:
            entity = await client.get_entity(event.chat_id)
            if isinstance(entity, Channel):
                await client(LeaveChannelRequest(entity))
                logger.info(f"[TEMP-MAIN] [{name}] Left channel")
        except Exception:
            pass
        await asyncio.sleep(1)


async def _cmd_status(automation, event):
    """Handle /status command."""
    online = sum(
        1 for c in automation.clone_clients
        if c.is_connected()
    )
    loops = len(automation.loops.active)
    mirror = "ON ✓" if automation.mirror_mode else "OFF"
    await event.reply(
        f"📊 **Status** (Temp Main view)\n"
        f"Clones: {len(automation.clone_clients)} "
        f"({online} online)\n"
        f"Active Loops: {loops}\n"
        f"Mirror: {mirror}\n"
        f"Temp Main: {temp_main.temp_first_name} "
        f"(@{temp_main.temp_username})"
    )

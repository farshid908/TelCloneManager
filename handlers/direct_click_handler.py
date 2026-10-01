"""
Handler for direct click commands:
  click "A1"                                    — Main clicks on replied message
  click "A1" LINK                               — Main clicks on linked message
  click(all) "A1" LINK                          — Main + all clones click
  click(all) "A1" LINK -m                       — all clones without Main
  click(1 3 5) "A1" LINK                        — Main + Clone 1,3,5
  click(1 3 5) "A1" LINK -m                     — Clone 1,3,5 without Main
  All above also work with reply instead of LINK
  -n flag: auto-delete command message
"""

import asyncio
import logging
import re
from telethon import events

from click_engine import find_matching_button, handle_button_click

logger = logging.getLogger("TG-Auto")

CLICK_AUTO_DELETE_DELAY = 5.0


TG_LINK_RE = re.compile(
    r'https?://t\.me/(?:c/(?P<chat_id>\d+)|(?P<username>\w+))/(?P<msg_id>\d+)'
)





DIRECT_CLICK_RE = re.compile(
    r"""^click(?:\((?P<clones>[^)]*)\))?\s*(?:/(?P<chain_delay>\d+)\s+)?(?P<q>["'])(?P<button>.+?)(?P=q)(?:\s+(?P<rest>.+))?\s*$""",
    re.IGNORECASE | re.DOTALL,
)

REPEAT_CLICK_RE = re.compile(
    r"""^/?(?P<count>\d+)click(?:\((?P<clones>[^)]*)\))?\s+"""
    r"""(?:/(?P<delay>\d+)\s+)?(?P<q>["'])(?P<button>.+?)(?P=q)"""
    r"""(?:\s+(?P<rest>.+))?\s*$""",
    re.IGNORECASE | re.DOTALL,
)

CLICK_COUNT_RE = re.compile(
    r'''^click(?P<count>\d+)\((?P<clones>[^)]*)\)\s+'''
    r'''(?:/(?P<delay>\d+)\s+)?(?P<q>["'])(?P<button>.+?)(?P=q)'''
    r'''(?:\s+/(?P<post_delay>\d+))?'''
    r'''(?:\s+(?P<rest>.+))?\s*$''',
    re.IGNORECASE | re.DOTALL,
)

NO_MAIN_RE = re.compile(r'-m\b', re.IGNORECASE)
NO_TRACE_RE = re.compile(r'-n\b', re.IGNORECASE)




EDIT_REPEAT_RE = re.compile(r'^/edit(?P<count>\d+)$', re.IGNORECASE)


def _message_link(entity, chat_id, message_id):
    """Build the best Telegram link available for a message."""
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"

    
    raw_id = str(chat_id)
    if raw_id.startswith("-100"):
        raw_id = raw_id[4:]
    elif raw_id.startswith("-"):
        raw_id = raw_id[1:]
    return f"https://t.me/c/{raw_id}/{message_id}"


async def _notify_edit_timeout(link, session_name, count):
    """Tell the admin bot that the target was not edited in time."""
    try:
        from clone_settings.bot_core import get_bot_client, get_admin_user_id

        bot = get_bot_client()
        admin_id = get_admin_user_id()
        if bot is None or admin_id is None:
            logger.warning(
                "[CLICK-EDIT] aiogram notification unavailable "
                "(bot=%s admin=%s)", bool(bot), admin_id,
            )
            return
        text = (
            "Message was not edited.\n"
            f"Session: {session_name}\n"
            f"Clicks completed: {count}\n"
            f"Message link: {link}"
        )
        await bot.send_message(admin_id, text)
        logger.info(
            "[CLICK-EDIT] Sent aiogram timeout notification for %s",
            link,
        )
    except Exception as exc:
        logger.warning(
            "[CLICK-EDIT] Could not send aiogram timeout notification: %s",
            exc,
            exc_info=True,
        )


async def _repeat_until_edit(
    client, session_name, target_chat_id, message_id, button_text,
    count, link,
):
    """Click one message repeatedly and stop immediately when it is edited."""
    edited = asyncio.Event()

    async def _on_edit(event):
        if event.id == message_id:
            edited.set()
            logger.info(
                '[CLICK-EDIT] [%s] Target message %s was edited; stopping',
                session_name, message_id,
            )

    client.add_event_handler(
        _on_edit,
        events.MessageEdited(chats=target_chat_id),
    )
    clicked = 0
    try:
        entity = await client.get_entity(target_chat_id)
        for attempt in range(count):
            if edited.is_set():
                break
            message = await client.get_messages(entity, ids=message_id)
            if not message or not message.buttons:
                logger.warning(
                    '[CLICK-EDIT] [%s] Target message has no buttons',
                    session_name,
                )
                break
            match = find_matching_button(message, button_text)
            if match is None:
                logger.warning(
                    '[CLICK-EDIT] [%s] Button "%s" not found',
                    session_name, button_text,
                )
                break
            row_idx, col_idx, button, _, _ = match
            try:
                await message.click(row_idx, col_idx)
                clicked += 1
                logger.info(
                    '[CLICK-EDIT] [%s] Clicked "%s" (%s/%s)',
                    session_name, (button.text or '').strip(), clicked, count,
                )
            except Exception as exc:
                logger.warning(
                    '[CLICK-EDIT] [%s] Click %s failed: %s',
                    session_name, attempt + 1, exc,
                )
                break

            
            
            try:
                await asyncio.wait_for(edited.wait(), timeout=0.35)
            except asyncio.TimeoutError:
                pass

        if edited.is_set():
            return clicked, True
        await _notify_edit_timeout(None, link, session_name, clicked)
        return clicked, False
    finally:
        client.remove_event_handler(_on_edit)


async def _auto_delete_after(client, chat_id, msg_ids, delay):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
    except Exception:
        pass


async def _resolve_link(client, link_text):
    """
    Parse and resolve a telegram message link.
    Returns (message_object, chat_id) or (None, None).
    """
    clean_link = link_text.strip().strip("[]<>()")

    m = TG_LINK_RE.search(clean_link)
    if not m:
        logger.error(f"[CLICK-DIRECT] Invalid link: {clean_link}")
        return None, None

    msg_id = int(m.group("msg_id"))
    entity = None

    if m.group("chat_id"):
        raw_id = int(m.group("chat_id"))
        for cid in [raw_id, -raw_id, int(f"-100{raw_id}")]:
            try:
                entity = await client.get_entity(cid)
                logger.info(f"[CLICK-DIRECT] Resolved private chat: {cid}")
                break
            except Exception:
                continue

    elif m.group("username"):
        uname = m.group("username")
        try:
            entity = await client.get_entity(uname)
            logger.info(f"[CLICK-DIRECT] Resolved public: @{uname}")
        except Exception as e:
            logger.error(f"[CLICK-DIRECT] Can't resolve @{uname}: {e}")

    if entity is None:
        return None, None

    try:
        message = await client.get_messages(entity, ids=msg_id)
        if message:
            return message, entity.id
    except Exception as e:
        logger.error(f"[CLICK-DIRECT] Get message failed: {e}")

    return None, None


def _parse_clone_selector(raw):
    """Parse clone selector: 'all', '1 3 5', '1-5'."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    if raw.lower() == "all":
        return "all"
    parts = re.split(r'[,\s]+', raw)
    indices = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        range_match = re.match(r'^(\d+)-(\d+)$', part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start <= end:
                indices.extend(range(start, end + 1))
            else:
                indices.extend(range(start, end - 1, -1))
        elif part.isdigit():
            indices.append(int(part))
    if not indices:
        return None
    seen = set()
    unique = []
    for idx in indices:
        if idx not in seen and idx >= 1:
            seen.add(idx)
            unique.append(idx)
    return unique if unique else None


def _build_session_list(automation, clone_indices, include_main):
    """Build list of (client, name) based on clone selector and -m flag."""
    sessions = []

    if clone_indices is None:
        
        if include_main:
            sessions.append((automation.main_client, "Main"))
        return sessions

    if clone_indices == "all":
        
        if include_main:
            sessions.append((automation.main_client, "Main"))
        for c, n in zip(automation.clone_clients, automation.clone_names):
            sessions.append((c, n))
        return sessions

    
    if include_main:
        sessions.append((automation.main_client, "Main"))

    skipped = []
    for idx in clone_indices:
        zero_idx = idx - 1
        if 0 <= zero_idx < len(automation.clone_clients):
            sessions.append((
                automation.clone_clients[zero_idx],
                automation.clone_names[zero_idx],
            ))
        else:
            skipped.append(idx)

    if skipped:
        logger.warning(f"[CLICK-DIRECT] Skipped invalid indices: {skipped}")

    return sessions


async def _find_reply_owner_clone(automation, target_msg):
    """Find the clone that authored the message being replied to."""
    owner_id = getattr(target_msg, "sender_id", None)
    if getattr(target_msg, "reply_to_msg_id", None):
        try:
            replied = await target_msg.get_reply_message()
            owner_id = getattr(replied, "sender_id", None) or owner_id
        except Exception:
            pass
    if owner_id is None:
        return None

    for index, client in enumerate(automation.clone_clients):
        try:
            me = await client.get_me()
            if me and me.id == owner_id:
                return index
        except Exception:
            continue
    return None


async def _repeat_click_on_message(
    client, session_name, chat_id, message_id, button_text, count, delay
):
    clicked = 0
    for attempt in range(count):
        message = await client.get_messages(chat_id, ids=message_id)
        if not message or not message.buttons:
            break
        match = find_matching_button(message, button_text)
        if match is None:
            break
        row_idx, col_idx, button, _, _ = match
        try:
            await message.click(row_idx, col_idx)
        except Exception as exc:
            logger.warning(
                "[CLICK-REPEAT] [%s] Click %s failed: %s",
                session_name, attempt + 1, exc,
            )
            break
        clicked += 1
        logger.info(
            '[CLICK-REPEAT] [%s] Clicked "%s" (%s/%s)',
            session_name, (button.text or "").strip(), clicked, count,
        )
        if attempt < count - 1:
            await asyncio.sleep(delay)
    return clicked


async def _repeat_sessions(automation, clones_raw, target_msg=None):
    """Choose the target message owner for clickN() repeat commands.

    Empty parentheses mean: use the clone that authored the replied-to
    message. Explicit parentheses retain clone-selector behavior.
    """
    if not clones_raw or not clones_raw.strip():
        owner_index = await _find_reply_owner_clone(automation, target_msg)
        if owner_index is None:
            return []
        return [(
            automation.clone_clients[owner_index],
            automation.clone_names[owner_index],
        )]

    if clones_raw and clones_raw.strip():
        selected = _parse_clone_selector(clones_raw)
        indices = (
            range(1, len(automation.clone_clients) + 1)
            if selected == "all" else (selected or [])
        )
    else:
        indices = range(1, len(automation.clone_clients) + 1)

    result = []
    for index in indices:
        zero = index - 1
        if 0 <= zero < len(automation.clone_clients):
            result.append((
                automation.clone_clients[zero],
                automation.clone_names[zero],
            ))
    return result


async def _click_on_message_for_session(
    client,
    session_name,
    chat_id,
    msg_id,
    button_text,
    chain_delay=None,
):
    """
    Resolve the message for a specific session and click the button.
    Each session needs to resolve the message independently.
    """
    try:
        
        try:
            entity = await client.get_entity(chat_id)
        except Exception:
            
            try:
                entity = await client.get_entity(int(f"-100{chat_id}"))
            except Exception as e:
                logger.error(
                    f"[CLICK-DIRECT] [{session_name}] Can't resolve chat "
                    f"{chat_id}: {e}"
                )
                return False

        try:
            message = await client.get_messages(entity, ids=msg_id)
        except Exception as e:
            logger.error(
                f"[CLICK-DIRECT] [{session_name}] Can't get message "
                f"{msg_id}: {e}"
            )
            return False

        if message is None:
            logger.error(
                f"[CLICK-DIRECT] [{session_name}] Message {msg_id} not found"
            )
            return False

        if not message.buttons:
            logger.warning(
                f"[CLICK-DIRECT] [{session_name}] Message has no buttons"
            )
            return False

        return await handle_button_click(
            client=client,
            chat_id=chat_id,
            button_text=button_text,
            session_name=session_name,
            after_msg_id=message.id,
            chain_delay=chain_delay,
            initial_message=message,
        )

    except Exception as e:
        logger.error(
            f"[CLICK-DIRECT] [{session_name}] Error: "
            f"{type(e).__name__}: {e}"
        )
        return False


def register_direct_click_handler(automation):
    """Register direct click command handler."""
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        
        
        
        pattern=r"""(?i)^click(?!\d)[\s(]""",
        from_users=aid,
    ))
    async def _direct_click(event):
        try:
            if not automation.is_admin(event):
                return

            raw = event.raw_text.strip()

            
            
            
            if REPEAT_CLICK_RE.match(raw) or CLICK_COUNT_RE.match(raw):
                logger.debug(
                    "[CLICK-DIRECT] Ignoring repeat-click command: %s",
                    raw,
                )
                return

            logger.info(f"[CLICK-DIRECT] ⚡ Command: {raw[:100]}")

            
            m = DIRECT_CLICK_RE.match(raw)
            if not m:
                logger.debug(f"[CLICK-DIRECT] Parse failed: {raw}")
                return  

            clones_raw = m.group("clones")
            button_text = m.group("button").strip()
            chain_delay = (
                int(m.group("chain_delay"))
                if m.group("chain_delay") is not None else None
            )
            rest = (m.group("rest") or "").strip()

            edit_match = EDIT_REPEAT_RE.fullmatch(rest)
            edit_count = int(edit_match.group("count")) if edit_match else None

            
            include_main = not bool(NO_MAIN_RE.search(rest))
            no_trace = bool(NO_TRACE_RE.search(rest))

            
            clean_rest = "" if edit_match else NO_MAIN_RE.sub("", rest)
            clean_rest = "" if edit_match else NO_TRACE_RE.sub("", clean_rest).strip()

            
            clone_indices = _parse_clone_selector(clones_raw)

            
            target_msg = None
            target_chat_id = None

            if clean_rest and TG_LINK_RE.search(clean_rest):
                
                logger.info(f"[CLICK-DIRECT] Link mode: {clean_rest}")

                target_msg, target_chat_id = await _resolve_link(
                    automation.main_client, clean_rest,
                )

                if target_msg is None:
                    await event.reply(
                        "⚠️ Cannot resolve message link"
                    )
                    asyncio.create_task(
                        _auto_delete_after(
                            automation.main_client, event.chat_id,
                            [event.id], CLICK_AUTO_DELETE_DELAY,
                        )
                    )
                    return

            else:
                
                if not event.is_reply:
                    warn = await event.reply(
                        "⚠️ Reply to a message or provide a link:\n"
                        '`click "A1"` (reply)\n'
                        '`click "A1" https://t.me/group/123`\n'
                        '`click(all) "A1" LINK`\n'
                        '`click(1 3 5) "A1" LINK -m`'
                    )
                    asyncio.create_task(
                        _auto_delete_after(
                            automation.main_client, event.chat_id,
                            [event.id, warn.id],
                            CLICK_AUTO_DELETE_DELAY,
                        )
                    )
                    return

                target_msg = await event.get_reply_message()
                if target_msg is None:
                    warn = await event.reply("⚠️ Cannot find replied message")
                    asyncio.create_task(
                        _auto_delete_after(
                            automation.main_client, event.chat_id,
                            [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                        )
                    )
                    return

                target_chat_id = event.chat_id
                logger.info(
                    f"[CLICK-DIRECT] Reply mode: msg={target_msg.id}"
                )

            
            
            if clones_raw is not None and not clones_raw.strip():
                if not event.is_reply:
                    warn = await event.reply(
                        '⚠️ `click()` requires a reply to a clone message.'
                    )
                    asyncio.create_task(_auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                    ))
                    return
                owner_index = await _find_reply_owner_clone(
                    automation, target_msg
                )
                if owner_index is None:
                    warn = await event.reply(
                        "⚠️ Could not identify which clone authored this message."
                    )
                    asyncio.create_task(_auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                    ))
                    return
                sessions = [(
                    automation.clone_clients[owner_index],
                    automation.clone_names[owner_index],
                )]
                logger.info(
                    "[CLICK-DIRECT] click() selected %s",
                    automation.clone_names[owner_index],
                )
            elif edit_count is not None and clones_raw and clones_raw.strip().isdigit():
                
                
                clone_index = int(clones_raw.strip())
                zero_idx = clone_index - 1
                if not 0 <= zero_idx < len(automation.clone_clients):
                    warn = await event.reply(
                        f"⚠️ Clone {clone_index} not found."
                    )
                    asyncio.create_task(_auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                    ))
                    return
                sessions = [(
                    automation.clone_clients[zero_idx],
                    automation.clone_names[zero_idx],
                )]
                logger.info(
                    "[CLICK-EDIT] Selected %s for /edit%s",
                    automation.clone_names[zero_idx], edit_count,
                )
            else:
                sessions = _build_session_list(
                    automation, clone_indices, include_main,
                )

            if not sessions:
                warn = await event.reply("⚠️ No sessions selected")
                asyncio.create_task(_auto_delete_after(
                    automation.main_client, event.chat_id,
                    [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                ))
                return

            
            if not target_msg.buttons:
                warn = await event.reply("⚠️ Message has no buttons")
                asyncio.create_task(
                    _auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                    )
                )
                return

            
            session_names = [n for _, n in sessions]
            logger.info(
                f"[CLICK-DIRECT] Sessions: {', '.join(session_names)} "
                f"→ button \"{button_text}\" on msg {target_msg.id}"
            )

            
            
            if edit_count is not None:
                if len(sessions) != 1:
                    warn = await event.reply(
                        '⚠️ /edit mode requires `click()` or `click(number)`.'
                    )
                    asyncio.create_task(_auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
                    ))
                    return

                session_client, session_name = sessions[0]
                try:
                    target_entity = await automation.main_client.get_entity(
                        target_chat_id
                    )
                except Exception:
                    target_entity = None
                link = _message_link(
                    target_entity, target_chat_id, target_msg.id,
                )
                await _repeat_until_edit(
                    session_client,
                    session_name,
                    target_chat_id,
                    target_msg.id,
                    button_text,
                    edit_count,
                    link,
                )
                asyncio.create_task(_auto_delete_after(
                    automation.main_client, event.chat_id,
                    [event.id], CLICK_AUTO_DELETE_DELAY,
                ))
                return

            
            success = 0
            failed = 0

            for client, session_name in sessions:
                if session_name == "Main":
                    
                    match = find_matching_button(target_msg, button_text)
                    if match:
                        row_idx, col_idx, button, _, _ = match
                        btn_label = (button.text or "").strip()
                        coord = f"{chr(65 + row_idx)}{col_idx + 1}"
                        try:
                            ok = await handle_button_click(
                                client=automation.main_client,
                                chat_id=target_chat_id,
                                button_text=button_text,
                                session_name="Main",
                                after_msg_id=target_msg.id,
                                chain_delay=chain_delay,
                                initial_message=target_msg,
                            )
                            success += 1 if ok else 0
                            failed += 0 if ok else 1
                        except Exception as e:
                            logger.error(
                                f"[CLICK-DIRECT] [Main] ✗ Click failed: {e}"
                            )
                            failed += 1
                    else:
                        logger.warning(
                            f"[CLICK-DIRECT] [Main] Button not found"
                        )
                        failed += 1
                else:
                    
                    ok = await _click_on_message_for_session(
                        client=client,
                        session_name=session_name,
                        chat_id=target_chat_id,
                        msg_id=target_msg.id,
                        button_text=button_text,
                        chain_delay=chain_delay,
                    )
                    if ok:
                        success += 1
                    else:
                        failed += 1

                
                await asyncio.sleep(0.3)

            logger.info(
                f"[CLICK-DIRECT] Complete: ✓{success} ✗{failed}"
            )

            
            if no_trace or True:  
                asyncio.create_task(
                    _auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id], CLICK_AUTO_DELETE_DELAY,
                    )
                )

        except Exception as e:
            logger.error(
                f"[CLICK-DIRECT] Handler error: {type(e).__name__}: {e}",
                exc_info=True,
            )

    @automation.main_client.on(events.NewMessage(
        pattern=r"""(?i)^(?:/?\d+click[\s(]|click\d+\()""",
        from_users=aid,
    ))
    async def _repeat_click(event):
        if not automation.is_admin(event):
            return

        raw = (event.raw_text or "").strip()
        match = REPEAT_CLICK_RE.match(raw)
        click_count_match = None if match else CLICK_COUNT_RE.match(raw)
        normalized = match or click_count_match
        if not normalized:
            return
        if not event.is_reply:
            warn = await event.reply(
                "⚠️ Reply to the target message.\n"
                'Example: `5click(2) /5 "A1"`'
            )
            asyncio.create_task(_auto_delete_after(
                automation.main_client, event.chat_id,
                [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
            ))
            return

        target_msg = await event.get_reply_message()
        if target_msg is None:
            return

        count = int(normalized.group("count"))
        delay = int(
            normalized.group("delay")
            or normalized.groupdict().get("post_delay")
            or 0
        )
        button = normalized.group("button").strip()
        clones_raw = normalized.group("clones")
        sessions = await _repeat_sessions(
            automation, clones_raw, target_msg
        )

        
        
        if clones_raw and not sessions:
            warn = await event.reply("⚠️ No valid clone selected.")
            asyncio.create_task(_auto_delete_after(
                automation.main_client, event.chat_id,
                [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
            ))
            return
        if not sessions:
            warn = await event.reply(
                "⚠️ Could not identify the clone that sent the replied-to message."
            )
            asyncio.create_task(_auto_delete_after(
                automation.main_client, event.chat_id,
                [event.id, warn.id], CLICK_AUTO_DELETE_DELAY,
            ))
            return

        try:
            await event.delete()
        except Exception:
            pass

        logger.info(
            '[CLICK-REPEAT] Command received: %s; sessions=%s',
            raw,
            ", ".join(name for _, name in sessions),
        )
        for client, name in sessions:
            await _repeat_click_on_message(
                client,
                name,
                event.chat_id,
                target_msg.id,
                button,
                count,
                delay,
            )

        logger.info("[CLICK-REPEAT] Completed: %s", raw)

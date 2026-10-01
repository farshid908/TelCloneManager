"""
Safe message sender with support for:
  - Simple text sending
  - Button clicks
  - Dclick (click until disappear)
  - Reply to bot
  - If/elif/else conditional logic
  - Bridge fallback for private users
"""

import asyncio
import logging
import time
from typing import Dict, Optional

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    UserIsBlockedError,
)

from entity_resolver import (
    ChatTarget,
    resolve_entity,
    resolve_entity_with_bridge,
)
from click_engine import handle_button_click, find_matching_button
from condition_engine import evaluate_condition, wait_for_bot_reply

logger = logging.getLogger("TG-Auto")

# A bridge operation is expensive (marker + propagation wait + clone dialog
# refresh). Reuse a successful preparation for a short period.
_BRIDGE_CACHE_TTL = 600
_bridge_cache_until = {}


async def _execute_action(
    client, chat_id, session_name,
    bot_reply_msg, action, click_after_msg_id,
):
    """Execute a click and/or reply action."""
    if not action:
        return

    if action.get("click"):
        logger.info(f"[ACTION] [{session_name}] Click: \"{action['click']}\"")
        await handle_button_click(
            client=client, chat_id=chat_id,
            button_text=action["click"], session_name=session_name,
            after_msg_id=click_after_msg_id,
            reply_to_msg_id=click_after_msg_id,
            chain_delay=action.get("click_chain_delay"),
        )

    if action.get("reply") and bot_reply_msg:
        from handlers.reply_handler import send_reply
        logger.info(f"[ACTION] [{session_name}] Reply: \"{action['reply']}\"")
        await send_reply(
            client=client, chat_id=chat_id,
            target_message=bot_reply_msg,
            reply_text=action["reply"], session_name=session_name,
        )


async def _handle_if_structure(
    client, chat_id, session_name, sent_msg_id, if_struct,
):
    """Handle an if/elif/else structure after the initial send."""
    logger.info(f"[IF] [{session_name}] Waiting for bot reply…")
    first_reply_msg = await wait_for_bot_reply(
        client=client, chat_id=chat_id,
        session_name=session_name, after_msg_id=sent_msg_id,
    )

    if first_reply_msg is None:
        logger.warning(f"[IF] [{session_name}] No reply — skipping if")
        return

    condition_msg = first_reply_msg

    if if_struct["type"] == "nested":
        nested_cmd = if_struct["nested_command"]
        import re
        nested_match = re.match(
            r"""^send\s+(["'])(.+?)\1""",
            nested_cmd.strip(), re.IGNORECASE,
        )
        if not nested_match:
            logger.error(f"[IF] [{session_name}] Invalid nested: {nested_cmd}")
            return

        nested_text = nested_match.group(2)

        entity = await resolve_entity(client, chat_id, session_name)
        if entity is None:
            logger.error(f"[IF] [{session_name}] Cannot resolve entity")
            return

        try:
            nested_sent = await client.send_message(entity, nested_text)
            logger.info(
                f"[IF] [{session_name}] Sent nested \"{nested_text}\" "
                f"(msg_id={nested_sent.id})"
            )
        except Exception as e:
            logger.error(f"[IF] [{session_name}] Nested send failed: {e}")
            return

        logger.info(f"[IF] [{session_name}] Waiting for second reply…")
        second_reply_msg = await wait_for_bot_reply(
            client=client, chat_id=chat_id,
            session_name=session_name, after_msg_id=nested_sent.id,
        )

        if second_reply_msg is None:
            logger.warning(f"[IF] [{session_name}] No second reply")
            return

        condition_msg = second_reply_msg

    condition_result = evaluate_condition(if_struct["condition"], condition_msg)
    click_after = sent_msg_id

    if condition_result:
        logger.info(f"[IF] [{session_name}] ✓ TRUE → then")
        await _execute_action(
            client, chat_id, session_name,
            condition_msg, if_struct["then"], click_after,
        )
    elif if_struct["elif"]:
        logger.info(f"[IF] [{session_name}] ✗ FALSE → elif")
        await _execute_action(
            client, chat_id, session_name,
            condition_msg, if_struct["elif"], click_after,
        )
    elif if_struct["else"]:
        logger.info(f"[IF] [{session_name}] ✗ FALSE → else")
        await _execute_action(
            client, chat_id, session_name,
            condition_msg, if_struct["else"], click_after,
        )
    else:
        logger.info(f"[IF] [{session_name}] ✗ FALSE → no action")


async def _handle_dclick(
    client,
    chat_id,
    session_name,
    sent_msg_id,
    dclick_text,
    dclick_delay,
):
    """
    Click a button repeatedly until it disappears.

    Strategy:
      1. Wait for bot reply (message with buttons)
      2. Find the matching button
      3. Click it
      4. Wait dclick_delay seconds
      5. Re-fetch the message to check if button still exists
      6. Repeat until button is gone
    """
    logger.info(
        f"[DCLICK] [{session_name}] Clicking \"{dclick_text}\" "
        f"until gone (delay={dclick_delay}s)"
    )

    # Wait for bot reply first
    bot_reply = await wait_for_bot_reply(
        client=client,
        chat_id=chat_id,
        session_name=session_name,
        after_msg_id=sent_msg_id - 1,
    )

    if bot_reply is None:
        logger.info(f"[DCLICK] [{session_name}] No reply — stopping")
        return

    if not bot_reply.buttons:
        logger.info(f"[DCLICK] [{session_name}] Reply has no buttons — stopping")
        return

    target_msg = bot_reply
    click_count = 0

    while True:
        # Find the button on current message
        match = find_matching_button(target_msg, dclick_text)

        if match is None:
            if click_count > 0:
                logger.info(
                    f"[DCLICK] [{session_name}] Button disappeared — "
                    f"done! ({click_count} click(s))"
                )
            else:
                logger.info(
                    f"[DCLICK] [{session_name}] Button \"{dclick_text}\" "
                    f"not found — stopping"
                )
            break

        row_idx, col_idx, button, matched, rank = match
        btn_label = (button.text or "").strip()

        # Click it
        try:
            await target_msg.click(row_idx, col_idx)
            click_count += 1
            logger.info(
                f"[DCLICK] [{session_name}] ✓ Clicked \"{btn_label}\" "
                f"(#{click_count})"
            )
        except Exception as e:
            logger.error(
                f"[DCLICK] [{session_name}] Click failed: "
                f"{type(e).__name__}: {e}"
            )
            break

        # Wait before next attempt
        if dclick_delay > 0:
            logger.info(
                f"[DCLICK] [{session_name}] Waiting {dclick_delay}s…"
            )
            await asyncio.sleep(dclick_delay)

        # Re-fetch the message to see if button still exists
        try:
            fresh_msg = await client.get_messages(
                chat_id, ids=target_msg.id,
            )

            if fresh_msg is None:
                logger.info(
                    f"[DCLICK] [{session_name}] Message deleted — done!"
                )
                break

            if not fresh_msg.buttons:
                logger.info(
                    f"[DCLICK] [{session_name}] All buttons gone — done! "
                    f"({click_count} click(s))"
                )
                break

            # Update target for next iteration
            target_msg = fresh_msg

        except Exception as e:
            logger.error(
                f"[DCLICK] [{session_name}] Re-fetch failed: "
                f"{type(e).__name__}: {e}"
            )
            break

    logger.info(
        f"[DCLICK] [{session_name}] ✓ Complete: {click_count} click(s) total"
    )


async def pre_bridge_target(
    target,
    main_client,
    bridge_group_id,
    all_clone_clients,
    all_clone_names,
):
    """
    Pre-bridge a target BEFORE sending from clones.
    Returns the (possibly updated) send target.
    """
    if main_client is None:
        return target

    if not all_clone_clients:
        return target

    if isinstance(target, str) and target.startswith("@"):
        return target

    cache_key = str(target)
    if _bridge_cache_until.get(cache_key, 0) > time.monotonic():
        logger.debug(f"[SEND-PREP] Reusing bridge cache for {target}")
        return target

    # Try to get @username from Main
    try:
        entity = await main_client.get_entity(target)
        username = getattr(entity, "username", None)
        if username:
            new_target = f"@{username}"
            logger.info(
                f"[SEND-PREP] Main found @username for {target}: {new_target}"
            )
            _bridge_cache_until[cache_key] = time.monotonic() + _BRIDGE_CACHE_TTL
            return new_target
    except Exception as e:
        logger.debug(f"[SEND-PREP] Can't get Main entity for {target}: {e}")

    # Check if clones already know
    already_ok = 0
    for client in all_clone_clients:
        try:
            await client.get_entity(target)
            already_ok += 1
        except Exception:
            pass

    if already_ok == len(all_clone_clients):
        _bridge_cache_until[cache_key] = time.monotonic() + _BRIDGE_CACHE_TTL
        return target

    logger.info(
        f"[SEND-PREP] {len(all_clone_clients) - already_ok} clone(s) can't "
        f"resolve {target}"
    )

    # Try bridge
    try:
        entity = await main_client.get_entity(target)

        try:
            marker = await main_client.send_message(entity, "\u200b")
            await asyncio.sleep(0.5)
        except Exception:
            marker = None

        if marker and bridge_group_id is not None:
            try:
                from telethon.tl.functions.messages import ForwardMessagesRequest
                bridge_entity = await main_client.get_entity(bridge_group_id)
                await main_client(ForwardMessagesRequest(
                    from_peer=entity,
                    id=[marker.id],
                    to_peer=bridge_entity,
                    with_my_score=False,
                    drop_author=False,
                ))
                logger.info(f"[SEND-PREP] Forwarded marker to bridge")

                await asyncio.sleep(3)

                async def refresh(client, name):
                    try:
                        async for _ in client.iter_dialogs(limit=30):
                            pass
                    except Exception:
                        pass

                tasks = [
                    refresh(c, n)
                    for c, n in zip(all_clone_clients, all_clone_names)
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

                success = 0
                for client in all_clone_clients:
                    try:
                        await client.get_entity(target)
                        success += 1
                    except Exception:
                        pass

                logger.info(
                    f"[SEND-PREP] After bridge: {success}/{len(all_clone_clients)} "
                    f"clones can resolve"
                )

            except Exception as e:
                logger.error(f"[SEND-PREP] Bridge forward failed: {e}")

        if marker:
            try:
                await main_client.delete_messages(entity, [marker.id])
            except Exception:
                pass

    except Exception as e:
        logger.error(f"[SEND-PREP] Bridge strategy failed: {e}")

    # Even if one clone could not verify the peer, the bridge work has already
    # been performed. Avoid repeating the expensive operation for every next
    # message; the normal resolve fallback will handle an individual miss.
    _bridge_cache_until[cache_key] = time.monotonic() + _BRIDGE_CACHE_TTL
    return target


async def safe_send_message(
    client,
    chat_target,
    text,
    session_name="Unknown",
    reply_to=None,
    button_text=None,
    reply_text=None,
    if_structure=None,
    main_client=None,
    bridge_group_id=None,
    all_clone_clients=None,
    all_clone_names=None,
    override_target=None,
    dclick_text=None,
    dclick_delay=0,
    click_chain_delay=None,
    return_message=False,
):
    """
    Send a text message with full feature support:
      - Bridge fallback
      - If/elif/else
      - Button click
      - Dclick (click until disappear)
      - Reply
    """
    target_val = (
        override_target if override_target is not None
        else chat_target.send_target
    )
    kwargs = {}
    if reply_to:
        kwargs["reply_to"] = reply_to

    # Try silent resolve first
    entity = await resolve_entity(
        client, target_val, session_name=session_name, silent=True,
    )

    # Bridge fallback
    if entity is None and main_client is not None and bridge_group_id is not None:
        logger.info(
            f"[SEND] [{session_name}] {target_val} not cached — trying bridge…"
        )
        try:
            entity = await resolve_entity_with_bridge(
                client=client,
                target=target_val,
                session_name=session_name,
                main_client=main_client,
                bridge_group_id=bridge_group_id,
                other_clones=all_clone_clients,
                other_names=all_clone_names,
            )
        except Exception as e:
            logger.error(f"[SEND] [{session_name}] Bridge error: {e}")

    if entity is None:
        logger.error(
            f"[SEND] [{session_name}] ✗ Cannot resolve "
            f"'{chat_target.display_name}' ({target_val})"
        )
        return False

    # Send the message
    sent_msg = None
    for attempt in range(1, 4):
        try:
            sent_msg = await client.send_message(entity, text, **kwargs)
            logger.info(
                f"[SEND] [{session_name}] ✓ Sent to "
                f"'{chat_target.display_name}' (msg_id={sent_msg.id})"
            )
            break
        except FloodWaitError as e:
            logger.warning(f"[SEND] [{session_name}] FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
        except (ChatWriteForbiddenError, UserBannedInChannelError,
                UserIsBlockedError) as e:
            logger.error(f"[SEND] [{session_name}] ✗ Permission: {e}")
            return False
        except Exception as e:
            logger.error(
                f"[SEND] [{session_name}] ✗ Attempt {attempt}: "
                f"{type(e).__name__}: {e}"
            )
            if attempt < 3:
                await asyncio.sleep(2)

    if sent_msg is None:
        logger.error(f"[SEND] [{session_name}] ✗ All attempts failed")
        return False

    # ── IF structure (highest priority) ──────────────────────────
    if if_structure:
        await _handle_if_structure(
            client, chat_target.chat_id, session_name,
            sent_msg.id, if_structure,
        )
        return sent_msg if return_message else True

    # ── Simple button click ──────────────────────────────────────
    if button_text:
        logger.info(f"[CLICK] [{session_name}] Watching for \"{button_text}\"")
        await handle_button_click(
            client=client, chat_id=chat_target.chat_id,
            button_text=button_text, session_name=session_name,
            after_msg_id=sent_msg.id,
            # A bot may send its inline-keyboard response as a new message
            # without replying to the command message.  The sent-message
            # boundary is sufficient to avoid clicking stale buttons.
            reply_to_msg_id=None,
            chain_delay=click_chain_delay,
        )

    # ── Dclick (click until disappear) ───────────────────────────
    if dclick_text:
        await _handle_dclick(
            client=client,
            chat_id=chat_target.chat_id,
            session_name=session_name,
            sent_msg_id=sent_msg.id,
            dclick_text=dclick_text,
            dclick_delay=dclick_delay,
        )

    # ── Simple reply ─────────────────────────────────────────────
    if reply_text:
        from handlers.reply_handler import send_reply
        logger.info(f"[REPLY] [{session_name}] Waiting to reply…")
        reply_msg = await wait_for_bot_reply(
            client=client, chat_id=chat_target.chat_id,
            session_name=session_name, after_msg_id=sent_msg.id,
        )
        if reply_msg:
            await send_reply(
                client=client, chat_id=chat_target.chat_id,
                target_message=reply_msg,
                reply_text=reply_text, session_name=session_name,
            )

    return sent_msg if return_message else True

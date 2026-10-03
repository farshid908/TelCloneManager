"""
Click hunter — finds and clicks a button across ALL chats.

Modes:
  - Normal:      N clicks with delay
  - Edit mode:   click on every edit of the message
  - Loop mode:   keep watching forever (retries if button not found)
  - Disappear:   keep clicking until button vanishes
  - Combined:    any of the above with loop

Search strategy:
  - Initial scan: one-time scan across all dialogs
  - Then event-only: listen for NewMessage / MessageEdited events
    (no continuous scanning — efficient and instant)
"""

__TCM_FILE_HASH__ = "2719207094"


import asyncio
import logging
from typing import Optional, Tuple, Set

from telethon import TelegramClient, events
from telethon.errors import (
    BotResponseTimeoutError,
    FloodWaitError,
)

from click_engine import find_matching_button

logger = logging.getLogger("TG-Auto")



LOOP_RETRY_DELAY = 15
FULL_SCAN_DIALOGS = 100
FULL_SCAN_MESSAGES_PER_CHAT = 30






async def _interruptible_sleep(seconds: int, stop_flag: asyncio.Event):
    """Sleep that returns early if stop_flag is set."""
    if seconds <= 0:
        return
    if stop_flag is None:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(stop_flag.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass






async def find_button_in_all_chats(
    client: TelegramClient,
    button_text: str,
    session_name: str = "Unknown",
    dialog_limit: int = FULL_SCAN_DIALOGS,
    messages_per_chat: int = FULL_SCAN_MESSAGES_PER_CHAT,
) -> Optional[Tuple[object, int, int]]:
    """Search recent messages in all dialogs for a matching button."""
    try:
        dialogs = await client.get_dialogs(limit=dialog_limit)
    except Exception as e:
        logger.error(f"[HUNT] [{session_name}] Can't get dialogs: {e}")
        return None

    for dialog in dialogs:
        try:
            messages = await client.get_messages(
                dialog.entity, limit=messages_per_chat
            )

            for msg in messages:
                if not msg.buttons:
                    continue

                match = find_matching_button(msg, button_text)
                if match is None:
                    continue

                row_idx, col_idx, button, matched_target, match_rank = match
                btn_label = (button.text or "").strip()
                logger.info(
                    f"[HUNT] [{session_name}] Found \"{btn_label}\" in "
                    f"'{dialog.name}' (msg_id={msg.id})"
                )
                return (msg, row_idx, col_idx)

        except FloodWaitError as e:
            logger.warning(f"[HUNT] [{session_name}] FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            logger.debug(
                f"[HUNT] [{session_name}] Skipping '{dialog.name}': "
                f"{type(e).__name__}"
            )
            continue

    return None


async def click_once(
    client: TelegramClient,
    button_text: str,
    session_name: str = "Unknown",
) -> Tuple[bool, Optional[object], Optional[int], Optional[int]]:
    """Search all chats for the button and click it once (with fresh scan)."""
    result = await find_button_in_all_chats(
        client, button_text, session_name=session_name,
    )

    if result is None:
        return (False, None, None, None)

    msg, row_idx, col_idx = result
    button = msg.buttons[row_idx][col_idx]
    btn_label = (button.text or "").strip()

    try:
        await msg.click(row_idx, col_idx)
        logger.info(
            f"[HUNT] [{session_name}] ✓ Clicked \"{btn_label}\" (msg_id={msg.id})"
        )
        return (True, msg, row_idx, col_idx)
    except BotResponseTimeoutError:
        logger.warning(
            f"[HUNT] [{session_name}] Bot timeout after clicking \"{btn_label}\""
        )
        return (True, msg, row_idx, col_idx)
    except Exception as e:
        logger.error(
            f"[HUNT] [{session_name}] ✗ Click failed: {type(e).__name__}: {e}"
        )
        return (False, None, None, None)






async def hunt_and_click(
    client: TelegramClient,
    button_text: str,
    times: int,
    delay: int,
    session_name: str = "Unknown",
    progress_callback=None,
    start_from: int = 0,
    stop_flag=None,
    loop_mode: bool = False,
) -> int:
    """
    Click a button up to `times` times across all chats.

    times: how many times to click (0 = unlimited if loop_mode)
    delay: seconds between clicks
    loop_mode: if True, keep watching forever
    stop_flag: asyncio.Event for early stop
    start_from: skip first N clicks (for resume)
    """
    success_count = start_from
    i = start_from

    while True:
        if stop_flag is not None and stop_flag.is_set():
            logger.info(f"[HUNT] [{session_name}] Stop requested — exiting")
            break

        if times > 0 and success_count >= times:
            if loop_mode:
                logger.info(
                    f"[HUNT] [{session_name}] Reached {times} — loop resetting"
                )
                success_count = 0
                i = 0
                await _interruptible_sleep(LOOP_RETRY_DELAY, stop_flag)
                if stop_flag and stop_flag.is_set():
                    break
                continue
            else:
                break

        logger.info(
            f"[HUNT] [{session_name}] Click {i + 1}"
            f"{('/' + str(times)) if times > 0 else ' (∞)'} for \"{button_text}\"…"
        )

        try:
            clicked, msg, row, col = await click_once(
                client, button_text, session_name=session_name,
            )
        except Exception as e:
            logger.error(f"[HUNT] [{session_name}] click_once error: {e}")
            clicked = False

        if clicked:
            success_count += 1
            i += 1

            if progress_callback:
                try:
                    await progress_callback(success_count)
                except Exception as e:
                    logger.error(f"[HUNT] [{session_name}] Progress cb: {e}")

            if (times == 0 or success_count < times) and delay > 0:
                logger.info(f"[HUNT] [{session_name}] Waiting {delay}s…")
                await _interruptible_sleep(delay, stop_flag)
        else:
            if loop_mode:
                logger.info(
                    f"[HUNT] [{session_name}] Not found — waiting "
                    f"{LOOP_RETRY_DELAY}s and retrying"
                )
                await _interruptible_sleep(LOOP_RETRY_DELAY, stop_flag)
            else:
                logger.warning(
                    f"[HUNT] [{session_name}] Not found — exiting"
                )
                break

    logger.info(f"[HUNT] [{session_name}] ✓ Complete: {success_count} click(s)")
    return success_count






async def hunt_and_click_on_edit(
    client: TelegramClient,
    button_text: str,
    max_times: int = 0,
    session_name: str = "Unknown",
    progress_callback=None,
    stop_flag=None,
    initial_done: int = 0,
    loop_mode: bool = False,
) -> int:
    """
    Click on the button every time its message is edited.

    max_times: 0 = unlimited, N = stop after N clicks
    loop_mode: if True, keep listening after max reached / message gone
    """
    logger.info(
        f"[HUNT-EDIT] [{session_name}] Starting edit mode for \"{button_text}\" "
        f"(max_times={'∞' if max_times == 0 else max_times}, "
        f"done={initial_done}, loop={loop_mode})"
    )

    done_count = initial_done

    while True:
        if stop_flag is not None and stop_flag.is_set():
            break

        if max_times > 0 and done_count >= max_times and not loop_mode:
            break

        
        target_msg = None
        while target_msg is None:
            if stop_flag is not None and stop_flag.is_set():
                return done_count

            try:
                result = await find_button_in_all_chats(
                    client, button_text, session_name=session_name,
                )
            except Exception as e:
                logger.error(f"[HUNT-EDIT] Initial scan error: {e}")
                result = None

            if result is not None:
                target_msg, row_idx, col_idx = result
                break

            logger.info(
                f"[HUNT-EDIT] [{session_name}] Button not found, "
                f"waiting {LOOP_RETRY_DELAY}s"
            )
            await _interruptible_sleep(LOOP_RETRY_DELAY, stop_flag)

        target_msg_id = target_msg.id
        target_chat_id = target_msg.chat_id

        
        if max_times == 0 or done_count < max_times:
            try:
                await target_msg.click(row_idx, col_idx)
                done_count += 1
                logger.info(
                    f"[HUNT-EDIT] [{session_name}] ✓ Initial click "
                    f"({done_count}/{max_times if max_times else '∞'})"
                )
                if progress_callback:
                    try:
                        await progress_callback(done_count)
                    except Exception:
                        pass
            except BotResponseTimeoutError:
                done_count += 1
                if progress_callback:
                    try:
                        await progress_callback(done_count)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(
                    f"[HUNT-EDIT] [{session_name}] Initial click failed: {e}"
                )

        done_event = asyncio.Event()

        async def _on_edit(event):
            nonlocal done_count
            if stop_flag is not None and stop_flag.is_set():
                return
            if event.chat_id != target_chat_id:
                return
            if event.message.id != target_msg_id:
                return

            match = find_matching_button(event.message, button_text)
            if match is None:
                return

            new_row, new_col, new_button, _, _ = match
            btn_label = (new_button.text or "").strip()

            try:
                await event.message.click(new_row, new_col)
                done_count += 1
                logger.info(
                    f"[HUNT-EDIT] [{session_name}] ✓ Clicked on edit "
                    f"({done_count}/{max_times if max_times else '∞'}): "
                    f"\"{btn_label}\""
                )
                if progress_callback:
                    try:
                        await progress_callback(done_count)
                    except Exception:
                        pass
                if max_times > 0 and done_count >= max_times and not loop_mode:
                    done_event.set()
            except BotResponseTimeoutError:
                done_count += 1
                if progress_callback:
                    try:
                        await progress_callback(done_count)
                    except Exception:
                        pass
                if max_times > 0 and done_count >= max_times and not loop_mode:
                    done_event.set()
            except Exception as e:
                logger.error(
                    f"[HUNT-EDIT] [{session_name}] Edit click failed: {e}"
                )

        handler = client.on(events.MessageEdited(chats=target_chat_id))(_on_edit)

        logger.info(
            f"[HUNT-EDIT] [{session_name}] Listening for edits on msg "
            f"{target_msg_id}…"
        )

        try:
            while True:
                if stop_flag is not None and stop_flag.is_set():
                    break
                if max_times > 0 and done_count >= max_times and not loop_mode:
                    break

                try:
                    await asyncio.wait_for(done_event.wait(), timeout=5.0)
                    if max_times > 0 and done_count >= max_times and not loop_mode:
                        break
                    done_event.clear()
                except asyncio.TimeoutError:
                    continue

        finally:
            try:
                client.remove_event_handler(handler)
            except Exception:
                pass

        if not loop_mode:
            break

    logger.info(
        f"[HUNT-EDIT] [{session_name}] ✓ Complete: {done_count} clicks total"
    )
    return done_count






async def hunt_and_click_until_disappear(
    client: TelegramClient,
    button_text: str,
    delay: int,
    session_name: str = "Unknown",
    progress_callback=None,
    initial_done: int = 0,
    stop_flag=None,
    loop_mode: bool = False,
) -> int:
    """
    Keep clicking the button until it disappears.

    Strategy:
      1. Initial scan (only once): find any existing button
      2. Click it repeatedly with `delay` between clicks until gone
      3. Then, ONLY listen for new messages / edits (NO scanning)
      4. When a new message or edit contains the button → click it
      5. If loop_mode is False → exit after first disappearance

    delay: seconds between clicks
    loop_mode: if True, keep listening after button gone
    """
    logger.info(
        f"[HUNT-D] [{session_name}] Starting disappear mode for \"{button_text}\" "
        f"(delay={delay}s, loop={loop_mode}, done={initial_done})"
    )

    done_count = initial_done
    click_lock = asyncio.Lock()

    async def _click_message(msg) -> bool:
        """Try to click the button on a message. Returns True if success."""
        nonlocal done_count

        if not msg.buttons:
            return False

        match = find_matching_button(msg, button_text)
        if match is None:
            return False

        row_idx, col_idx, button, _, _ = match
        btn_label = (button.text or "").strip()

        try:
            await msg.click(row_idx, col_idx)
            done_count += 1
            logger.info(
                f"[HUNT-D] [{session_name}] ✓ Clicked \"{btn_label}\" "
                f"(msg_id={msg.id}, total={done_count})"
            )
            if progress_callback:
                try:
                    await progress_callback(done_count)
                except Exception as e:
                    logger.error(f"[HUNT-D] Progress cb error: {e}")
            return True
        except BotResponseTimeoutError:
            done_count += 1
            logger.warning(
                f"[HUNT-D] [{session_name}] Bot timeout for \"{btn_label}\""
            )
            if progress_callback:
                try:
                    await progress_callback(done_count)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.error(
                f"[HUNT-D] [{session_name}] Click failed \"{btn_label}\": {e}"
            )
            return False

    async def _spam_click_message(msg):
        """Keep clicking a message until button disappears from it."""
        while True:
            if stop_flag is not None and stop_flag.is_set():
                return

            
            try:
                fresh_msg = await client.get_messages(
                    msg.chat_id, ids=msg.id,
                )
            except Exception as e:
                logger.debug(f"[HUNT-D] Can't refetch: {e}")
                return

            if fresh_msg is None:
                logger.info(f"[HUNT-D] [{session_name}] Message deleted")
                return

            if not fresh_msg.buttons:
                logger.info(
                    f"[HUNT-D] [{session_name}] Button gone from msg "
                    f"{msg.id}"
                )
                return

            match = find_matching_button(fresh_msg, button_text)
            if match is None:
                logger.info(
                    f"[HUNT-D] [{session_name}] Button no longer matches"
                )
                return

            clicked = await _click_message(fresh_msg)
            if not clicked:
                return

            if delay > 0:
                await _interruptible_sleep(delay, stop_flag)

    
    logger.info(
        f"[HUNT-D] [{session_name}] Doing initial scan for existing button…"
    )

    try:
        result = await find_button_in_all_chats(
            client, button_text, session_name=session_name,
        )
    except Exception as e:
        logger.error(f"[HUNT-D] Initial scan failed: {e}")
        result = None

    if result is not None:
        msg, row_idx, col_idx = result
        logger.info(
            f"[HUNT-D] [{session_name}] Initial button found — "
            f"clicking until gone"
        )
        async with click_lock:
            await _spam_click_message(msg)
    else:
        logger.info(f"[HUNT-D] [{session_name}] No existing button found")

    
    if not loop_mode:
        logger.info(
            f"[HUNT-D] [{session_name}] Not loop mode — exiting "
            f"(total: {done_count} clicks)"
        )
        return done_count

    if stop_flag is not None and stop_flag.is_set():
        return done_count

    
    logger.info(
        f"[HUNT-D] [{session_name}] Now listening ONLY for new messages/edits "
        f"(no more scanning)"
    )

    msg_queue: asyncio.Queue = asyncio.Queue()

    async def _on_any_new_message(event):
        """Triggered when ANY new message arrives in ANY chat."""
        if stop_flag is not None and stop_flag.is_set():
            return
        if not event.message.buttons:
            return

        match = find_matching_button(event.message, button_text)
        if match is None:
            return

        logger.info(
            f"[HUNT-D] [{session_name}] ⚡ New msg with button in chat "
            f"{event.chat_id} (msg {event.message.id})"
        )
        await msg_queue.put(event.message)

    async def _on_any_edit(event):
        """Triggered when any message is edited in any chat."""
        if stop_flag is not None and stop_flag.is_set():
            return
        if not event.message.buttons:
            return

        match = find_matching_button(event.message, button_text)
        if match is None:
            return

        logger.info(
            f"[HUNT-D] [{session_name}] ⚡ Edit with button in chat "
            f"{event.chat_id} (msg {event.message.id})"
        )
        await msg_queue.put(event.message)

    
    new_msg_handler = client.on(events.NewMessage())(_on_any_new_message)
    edit_handler = client.on(events.MessageEdited())(_on_any_edit)

    try:
        while True:
            if stop_flag is not None and stop_flag.is_set():
                logger.info(f"[HUNT-D] [{session_name}] Stop requested")
                break

            
            try:
                msg = await asyncio.wait_for(msg_queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue  

            
            async with click_lock:
                await _spam_click_message(msg)

            
            while not msg_queue.empty():
                try:
                    msg_queue.get_nowait()
                except Exception:
                    break

    except asyncio.CancelledError:
        logger.info(f"[HUNT-D] [{session_name}] Cancelled")
        raise
    except Exception as e:
        logger.error(
            f"[HUNT-D] [{session_name}] Fatal: {type(e).__name__}: {e}",
            exc_info=True,
        )
    finally:
        try:
            client.remove_event_handler(new_msg_handler)
        except Exception:
            pass
        try:
            client.remove_event_handler(edit_handler)
        except Exception:
            pass
        logger.info(f"[HUNT-D] [{session_name}] Listeners removed")

    logger.info(
        f"[HUNT-D] [{session_name}] ✓ Complete: {done_count} click(s) total"
    )
    return done_count

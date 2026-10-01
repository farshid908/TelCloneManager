import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple

from telethon import TelegramClient, events
from telethon.errors import BotResponseTimeoutError

from config import BUTTON_CLICK_TIMEOUT

logger = logging.getLogger("TG-Auto")

# Pattern for coordinate-based click like "A1", "B2", "C3"
COORD_PATTERN = re.compile(r'^([A-Za-z])(\d+)$')

# Max rows/cols supported (safety guard)
MAX_ROW_INDEX = 25  # A-Z
MAX_COL_INDEX = 99

# Chained-click timing.  A ``/>`` transition gives the bot time to edit the
# message and expose the next keyboard.  The optional /N prefix is the small
# delay before watching for that edit; the edit itself has its own timeout.
DEFAULT_CHAIN_EDIT_TIMEOUT = 20
DEFAULT_CHAIN_DELAY = 5


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

def parse_sequential_steps(raw: str) -> List[str]:
    """
    Split sequential click chain by '/>'.
    "hello/>hi/>sell" -> ["hello", "hi", "sell"]
    "A1/B2/>C3"       -> ["A1/B2", "C3"]
    """
    if not raw:
        return []
    return [part.strip() for part in raw.split("/>") if part.strip()]


def parse_click_sequence(raw: str, chain_delay: Optional[int] = None) -> List[Dict]:
    """Parse a click chain while preserving the transition operator.

    ``A1/>B1`` means click A1, wait for the message/keyboard to update, then
    click B1.  ``A1>>B1`` means move to B1 immediately, without the explicit
    pre-watch delay.  ``chain_delay`` comes from the optional ``/N`` before
    the quoted target and defaults to five seconds.
    """
    if not raw or not raw.strip():
        return []

    parts = re.split(r"(\/>|>>)", raw.strip())
    first = parts[0].strip()
    if not first:
        return []

    delay = DEFAULT_CHAIN_DELAY if chain_delay is None else max(0, int(chain_delay))
    steps = [{"target": first, "transition": None}]
    index = 1
    while index < len(parts):
        transition = parts[index]
        target = parts[index + 1].strip() if index + 1 < len(parts) else ""
        if not target:
            return []
        steps.append({
            "target": target,
            "transition": "edit" if transition == "/>" else "immediate",
            "delay": delay,
            "timeout": DEFAULT_CHAIN_EDIT_TIMEOUT,
        })
        index += 2
    return steps


def parse_click_targets(step: str) -> List[str]:
    """
    Split a single step by '/' for priority fallback.
    "A1/B2/hello" -> ["A1", "B2", "hello"]
    Last item has highest priority if multiple match.
    """
    if not step:
        return []
    return [part.strip() for part in step.split("/") if part.strip()]


def parse_coordinate(target: str) -> Optional[Tuple[int, int]]:
    """
    Parse coordinate format like "A1", "B2", "C3".
    Returns (row_index, col_index) or None.
    """
    match = COORD_PATTERN.match(target.strip())
    if not match:
        return None

    row_letter = match.group(1).upper()
    col_number = int(match.group(2))

    row_index = ord(row_letter) - ord('A')
    col_index = col_number - 1

    if row_index < 0 or row_index > MAX_ROW_INDEX:
        return None
    if col_index < 0 or col_index > MAX_COL_INDEX:
        return None

    return (row_index, col_index)


# ─────────────────────────────────────────────────────────────────────────────
# Button matching
# ─────────────────────────────────────────────────────────────────────────────

def get_text_match_rank(button_label: str, target_text: str) -> int:
    """
    Text-based match ranking:
      3 = exact match
      2 = target contained in button label
      1 = button label contained in target
      0 = no match
    """
    btn = (button_label or "").strip().lower()
    tgt = (target_text or "").strip().lower()
    if not btn or not tgt:
        return 0
    if btn == tgt:
        return 3
    if tgt in btn:
        return 2
    if btn in tgt:
        return 1
    return 0


def find_button_by_coordinate(
    message,
    row_index: int,
    col_index: int,
) -> Optional[Tuple[int, int, object, str]]:
    """
    Find a button by exact row and column index.
    Returns (row_idx, col_idx, button, coord_str) or None.
    """
    if not message.buttons:
        return None
    if row_index >= len(message.buttons):
        return None

    row = message.buttons[row_index]
    if col_index >= len(row):
        return None

    button = row[col_index]
    row_letter = chr(ord('A') + row_index)
    coord_str = f"{row_letter}{col_index + 1}"
    return (row_index, col_index, button, coord_str)


def find_matching_button(
    message,
    target_text: str,
) -> Optional[Tuple[int, int, object, str, int]]:
    """
    Find a button using priority chain with support for coordinates and text.

    Priority:
      - Higher priority_idx (later in chain) = preferred
      - Coordinate match always wins over text match
      - Among text matches, higher rank wins (3>2>1)

    Returns (row_idx, col_idx, button, matched_target, match_type) or None.
    match_type: 100 = coordinate, 3/2/1 = text match rank
    """
    if not message.buttons:
        return None

    targets = parse_click_targets(target_text)
    if not targets:
        return None

    best = None
    best_priority_idx = -1
    best_match_type = -1

    for priority_idx, target in enumerate(targets):
        coord = parse_coordinate(target)

        if coord is not None:
            row_idx, col_idx = coord
            result = find_button_by_coordinate(message, row_idx, col_idx)
            if result:
                r_idx, c_idx, button, coord_str = result
                # Coordinate beats any text match at same or lower priority
                if (priority_idx > best_priority_idx
                        or (priority_idx == best_priority_idx
                            and 100 > best_match_type)):
                    best_priority_idx = priority_idx
                    best_match_type = 100
                    best = (r_idx, c_idx, button, coord_str, 100)
        else:
            for row_idx, row in enumerate(message.buttons):
                for col_idx, button in enumerate(row):
                    btn_label = (button.text or "").strip()
                    if not btn_label:
                        continue

                    match_rank = get_text_match_rank(btn_label, target)
                    if match_rank == 0:
                        continue

                    if (priority_idx > best_priority_idx
                            or (priority_idx == best_priority_idx
                                and match_rank > best_match_type)):
                        best_priority_idx = priority_idx
                        best_match_type = match_rank
                        best = (row_idx, col_idx, button, target, match_rank)

    return best


def describe_button_layout(message) -> str:
    """Generate a human-readable description of the button layout."""
    if not message.buttons:
        return "No buttons"

    lines = []
    for row_idx, row in enumerate(message.buttons):
        row_letter = chr(ord('A') + row_idx)
        buttons_desc = []
        for col_idx, button in enumerate(row):
            btn_text = (button.text or "?")[:20]
            coord = f"{row_letter}{col_idx + 1}"
            buttons_desc.append(f"[{coord}: {btn_text}]")
        lines.append(f"Row {row_letter}: {' '.join(buttons_desc)}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Click execution
# ─────────────────────────────────────────────────────────────────────────────

async def try_click_message(
    message,
    button_text: str,
    session_name: str,
) -> Tuple[bool, Optional[int]]:
    """
    Attempt to find and click a button on a specific message.
    Returns (success_bool, clicked_msg_id).
    """
    match = find_matching_button(message, button_text)
    if match is None:
        return (False, None)

    row_idx, col_idx, button, matched_target, match_type = match
    btn_label = (button.text or "").strip()
    row_letter = chr(ord('A') + row_idx)
    coord_str = f"{row_letter}{col_idx + 1}"

    if match_type == 100:
        match_desc = "coordinate"
    elif match_type == 3:
        match_desc = "exact text"
    elif match_type == 2:
        match_desc = "partial (target in button)"
    else:
        match_desc = "partial (button in target)"

    logger.info(
        f"[CLICK] [{session_name}] 🔘 Found \"{btn_label}\" "
        f"at [{coord_str}] ({match_desc}: \"{matched_target}\") "
        f"msg_id={message.id}"
    )

    try:
        await message.click(row_idx, col_idx)
        logger.info(
            f"[CLICK] [{session_name}] ✓ Clicked \"{btn_label}\" "
            f"at [{coord_str}] in msg_id={message.id}"
        )
        return (True, message.id)

    except BotResponseTimeoutError:
        logger.warning(
            f"[CLICK] [{session_name}] Bot timeout after clicking "
            f"\"{btn_label}\" at [{coord_str}] — treating as success"
        )
        return (True, message.id)

    except asyncio.CancelledError:
        raise

    except Exception as e:
        logger.error(
            f"[CLICK] [{session_name}] ✗ Click failed \"{btn_label}\" "
            f"at [{coord_str}]: {type(e).__name__}: {e}"
        )
        return (False, None)


# ─────────────────────────────────────────────────────────────────────────────
# Polling helper
# ─────────────────────────────────────────────────────────────────────────────

async def _poll_for_edits(
    client: TelegramClient,
    chat_id: int,
    step_target: str,
    session_name: str,
    after_msg_id: Optional[int],
    found_event: asyncio.Event,
    result: dict,
    poll_interval: float = 1.5,
    reply_to_msg_id: Optional[int] = None,
    allow_same_message: bool = False,
):
    """
    Periodically re-fetch recent messages to catch edits that added buttons.
    Runs until found_event is set or task is cancelled.
    """
    try:
        while not found_event.is_set():
            await asyncio.sleep(poll_interval)

            if found_event.is_set():
                break

            try:
                messages = await client.get_messages(chat_id, limit=5)
                for msg in messages:
                    if (
                        after_msg_id is not None
                        and (
                            msg.id < after_msg_id
                            if allow_same_message
                            else msg.id <= after_msg_id
                        )
                    ):
                        continue
                    if (
                        reply_to_msg_id is not None
                        and getattr(msg, "reply_to_msg_id", None)
                        != reply_to_msg_id
                    ):
                        continue
                    if not msg.buttons:
                        continue
                    if found_event.is_set():
                        break

                    success, clicked_id = await try_click_message(
                        msg, step_target, session_name
                    )
                    if success:
                        result["success"] = True
                        result["clicked_id"] = clicked_id
                        found_event.set()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    except asyncio.CancelledError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Single step handler
# ─────────────────────────────────────────────────────────────────────────────

async def _handle_single_step_click(
    client: TelegramClient,
    chat_id: int,
    step_target: str,
    session_name: str,
    after_msg_id: Optional[int],
    timeout: int,
    reply_to_msg_id: Optional[int] = None,
    allow_same_message: bool = False,
) -> Tuple[bool, Optional[int]]:
    """
    Handle one step of a click sequence (scan existing, then listen for new).
    Returns (success_bool, clicked_msg_id).
    """
    targets = parse_click_targets(step_target)
    target_descs = []
    for t in targets:
        coord = parse_coordinate(t)
        if coord:
            row_letter = chr(ord('A') + coord[0])
            target_descs.append(f"{row_letter}{coord[1] + 1}")
        else:
            target_descs.append(f'"{t}"')

    logger.info(
        f"[CLICK] [{session_name}] 🔍 Step target: "
        f"{' / '.join(target_descs)} in chat {chat_id} "
        f"(timeout: {timeout}s)"
    )

    # Phase 1: Check existing recent messages
    try:
        messages = await client.get_messages(chat_id, limit=5)
        for msg in messages:
            if (
                after_msg_id is not None
                and (
                    msg.id < after_msg_id
                    if allow_same_message
                    else msg.id <= after_msg_id
                )
            ):
                continue
            if (
                reply_to_msg_id is not None
                and getattr(msg, "reply_to_msg_id", None) != reply_to_msg_id
            ):
                continue
            if not msg.buttons:
                continue

            success, clicked_id = await try_click_message(
                msg, step_target, session_name
            )
            if success:
                return (True, clicked_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(
            f"[CLICK] [{session_name}] Existing message check failed: {e}"
        )

    # Phase 2: Listen for new messages AND edits
    found_event = asyncio.Event()
    result = {"success": False, "clicked_id": None}

    async def _on_new_message(event):
        if found_event.is_set():
            return
        if after_msg_id is not None and event.id < after_msg_id:
            return
        if (
            reply_to_msg_id is not None
            and getattr(event.message, "reply_to_msg_id", None)
            != reply_to_msg_id
        ):
            return
        if not event.message.buttons:
            return

        success, clicked_id = await try_click_message(
            event.message, step_target, session_name
        )
        if success:
            result["success"] = True
            result["clicked_id"] = clicked_id
            found_event.set()

    async def _on_message_edited(event):
        if found_event.is_set():
            return
        if after_msg_id is not None and event.id < after_msg_id:
            return
        if (
            reply_to_msg_id is not None
            and getattr(event.message, "reply_to_msg_id", None)
            != reply_to_msg_id
        ):
            return
        if not event.message.buttons:
            return

        logger.info(
            f"[CLICK] [{session_name}] Message {event.id} edited "
            f"— checking for buttons…"
        )
        success, clicked_id = await try_click_message(
            event.message, step_target, session_name
        )
        if success:
            result["success"] = True
            result["clicked_id"] = clicked_id
            found_event.set()

    new_handler = client.on(
        events.NewMessage(chats=chat_id)
    )(_on_new_message)

    edit_handler = client.on(
        events.MessageEdited(chats=chat_id)
    )(_on_message_edited)

    poll_task = None
    try:
        poll_task = asyncio.create_task(
            _poll_for_edits(
                client, chat_id, step_target, session_name,
                after_msg_id, found_event, result,
                reply_to_msg_id=reply_to_msg_id,
                allow_same_message=allow_same_message,
            )
        )

        try:
            await asyncio.wait_for(found_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"[CLICK] [{session_name}] ⏰ Timeout ({timeout}s) "
                f"— no button found for step: {' / '.join(target_descs)}"
            )

    except asyncio.CancelledError:
        raise

    finally:
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        try:
            client.remove_event_handler(new_handler)
        except Exception:
            pass
        try:
            client.remove_event_handler(edit_handler)
        except Exception:
            pass

    return (result["success"], result["clicked_id"])


# ─────────────────────────────────────────────────────────────────────────────
# Master orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def handle_button_click(
    client: TelegramClient,
    chat_id: int,
    button_text: str,
    session_name: str = "Unknown",
    after_msg_id: Optional[int] = None,
    reply_to_msg_id: Optional[int] = None,
    chain_delay: Optional[int] = None,
    initial_message=None,
) -> bool:
    """
    Master orchestrator for click sequences.

    Syntax:
      "A1"        → click at coordinate A1
      "A1/B2"     → try A1, fallback to B2 (same message view)
      "hello/>hi" → click "hello", wait for an edit (up to 20s), then "hi"
      "A1>>B2"    → click A1, immediately look for and click B2
      /N before the quoted target changes the pre-watch delay to N seconds.
    """
    sequence = parse_click_sequence(button_text, chain_delay=chain_delay)

    if not sequence:
        logger.warning(f"[CLICK] [{session_name}] Empty click target")
        return False

    logger.info(
        f"[CLICK] [{session_name}] 🎯 Starting {len(sequence)}-step sequence: "
        f"{' → '.join(step['target'] for step in sequence)}"
    )

    current_after_msg_id = after_msg_id
    total_success = True

    for i, step in enumerate(sequence):
        step_target = step["target"]
        step_num = i + 1

        logger.info(
            f"[CLICK] [{session_name}] ━━━ Step {step_num}/{len(sequence)}: "
            f"\"{step_target}\" ━━━"
        )

        try:
            if i == 0 and initial_message is not None:
                success, clicked_id = await try_click_message(
                    initial_message, step_target, session_name
                )
            else:
                transition = step.get("transition")
                if i > 0 and transition == "edit":
                    wait_before_watch = step.get("delay", DEFAULT_CHAIN_DELAY)
                    if wait_before_watch:
                        logger.info(
                            f"[CLICK] [{session_name}] ⏳ Waiting "
                            f"{wait_before_watch}s before watching for step "
                            f"{step_num}"
                        )
                        await asyncio.sleep(wait_before_watch)

                success, clicked_id = await _handle_single_step_click(
                    client=client,
                    chat_id=chat_id,
                    step_target=step_target,
                    session_name=session_name,
                    after_msg_id=current_after_msg_id,
                    timeout=(
                        step.get("timeout", DEFAULT_CHAIN_EDIT_TIMEOUT)
                        if i > 0 and transition == "edit"
                        else BUTTON_CLICK_TIMEOUT
                    ),
                    reply_to_msg_id=reply_to_msg_id,
                    allow_same_message=(i > 0),
                )
        except asyncio.CancelledError:
            logger.info(
                f"[CLICK] [{session_name}] Sequence cancelled at step {step_num}"
            )
            raise
        except Exception as e:
            logger.error(
                f"[CLICK] [{session_name}] Step {step_num} error: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            total_success = False
            break

        if not success:
            logger.warning(
                f"[CLICK] [{session_name}] ✗ Step {step_num} failed/timed out. "
                f"Aborting sequence."
            )
            total_success = False
            break

        # Update after_msg_id for next step
        if clicked_id is not None:
            current_after_msg_id = clicked_id

    if total_success:
        logger.info(
            f"[CLICK] [{session_name}] ✓ Full sequence completed successfully"
        )

    return total_success

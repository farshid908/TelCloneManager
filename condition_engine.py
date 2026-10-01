"""
Condition engine for the `if` command.

Supports patterns like:
    if line1 "hello"                  → check if line 1 contains "hello"
    if line4 X=Y                      → check if first two numbers in line 4 are equal
    if line~"keyword" X=Y             → find line containing "keyword", then check X=Y
    if line~"keyword" "value"         → find line containing "keyword", check contains "value"
    if any "hello"                    → check if ANY line contains "hello"
    if any X=Y                        → check if ANY line has two equal numbers
"""

import re
import logging
import asyncio
from typing import Optional, List

from telethon import events

from config import BUTTON_CLICK_TIMEOUT

logger = logging.getLogger("TG-Auto")






def get_message_lines(message) -> List[str]:
    """Split message text by newline."""
    if not message or not message.text:
        return []
    return message.text.split("\n")


def get_line(message, line_number: int) -> Optional[str]:
    """Get a specific line (1-indexed) from message."""
    lines = get_message_lines(message)
    idx = line_number - 1
    if 0 <= idx < len(lines):
        return lines[idx]
    return None


def find_line_containing(message, keyword: str) -> Optional[str]:
    """Find the first line that contains the keyword (case-insensitive)."""
    lines = get_message_lines(message)
    keyword_lower = keyword.lower().strip()
    for line in lines:
        if keyword_lower in line.lower():
            return line
    return None







CONDITION_LINE_TEXT = re.compile(
    r'^line(?P<line>\d+)\s+(?P<q>["\'])(?P<text>.+?)(?P=q)$',
    re.IGNORECASE,
)


CONDITION_LINE_COMPARE = re.compile(
    r'^line(?P<line>\d+)\s+(?P<var1>[A-Z])(?P<op>==|!=|=|>=|<=|>|<)(?P<var2>[A-Z])$',
    re.IGNORECASE,
)


CONDITION_LINE_SEARCH_COMPARE = re.compile(
    r'^line~(?P<q>["\'])(?P<keyword>.+?)(?P=q)\s+(?P<var1>[A-Z])(?P<op>==|!=|=|>=|<=|>|<)(?P<var2>[A-Z])$',
    re.IGNORECASE,
)


CONDITION_LINE_SEARCH_TEXT = re.compile(
    r'^line~(?P<q1>["\'])(?P<keyword>.+?)(?P=q1)\s+(?P<q2>["\'])(?P<text>.+?)(?P=q2)$',
    re.IGNORECASE,
)


CONDITION_ANY_TEXT = re.compile(
    r'^any\s+(?P<q>["\'])(?P<text>.+?)(?P=q)$',
    re.IGNORECASE,
)


CONDITION_ANY_COMPARE = re.compile(
    r'^any\s+(?P<var1>[A-Z])(?P<op>==|!=|=|>=|<=|>|<)(?P<var2>[A-Z])$',
    re.IGNORECASE,
)


def extract_first_numbers(text: str, count: int = 2) -> List[float]:
    """Extract the first N numbers from text (anywhere in the line)."""
    numbers = re.findall(r'-?\d+(?:\.\d+)?', text)
    result = []
    for n in numbers[:count]:
        try:
            result.append(float(n))
        except ValueError:
            pass
    return result


def compare_numbers(x: float, y: float, op: str) -> bool:
    """Compare two numbers with the given operator."""
    if op in ("=", "=="):
        return x == y
    elif op == "!=":
        return x != y
    elif op == ">":
        return x > y
    elif op == "<":
        return x < y
    elif op == ">=":
        return x >= y
    elif op == "<=":
        return x <= y
    return False






def evaluate_condition(condition_str: str, message) -> bool:
    """
    Evaluate a condition against a message.

    Supported patterns:
        line1 "hello"            → line 1 contains "hello"
        line4 X=Y                → line 4 has 2 numbers, X op Y
        line~"keyword" X=Y       → find line with "keyword", check X op Y
        line~"keyword" "value"   → find line with "keyword", check contains "value"
        any "hello"              → any line contains "hello"
        any X=Y                  → any line has 2 numbers matching X op Y
    """
    condition_str = condition_str.strip()
    lines = get_message_lines(message)

    logger.info(
        f"[CONDITION] Evaluating: '{condition_str}' against "
        f"{len(lines)} lines"
    )
    for i, line in enumerate(lines, 1):
        logger.debug(f"[CONDITION]   Line {i}: {line[:60]}")

    
    m = CONDITION_LINE_SEARCH_COMPARE.match(condition_str)
    if m:
        keyword = m.group("keyword")
        op = m.group("op")

        line_content = find_line_containing(message, keyword)
        if line_content is None:
            logger.info(f"[CONDITION] ✗ No line found containing \"{keyword}\"")
            return False

        numbers = extract_first_numbers(line_content, count=2)
        if len(numbers) < 2:
            logger.info(
                f"[CONDITION] ✗ Line with \"{keyword}\" has < 2 numbers "
                f"(found: {numbers})"
            )
            return False

        x, y = numbers[0], numbers[1]
        result = compare_numbers(x, y, op)
        logger.info(
            f"[CONDITION] Line \"{line_content[:50]}\": "
            f"{x} {op} {y}? → {'✓' if result else '✗'}"
        )
        return result

    
    m = CONDITION_LINE_SEARCH_TEXT.match(condition_str)
    if m:
        keyword = m.group("keyword")
        target_text = m.group("text").lower().strip()

        line_content = find_line_containing(message, keyword)
        if line_content is None:
            logger.info(f"[CONDITION] ✗ No line found containing \"{keyword}\"")
            return False

        result = target_text in line_content.lower()
        logger.info(
            f"[CONDITION] Line \"{line_content[:50]}\" contains "
            f"\"{target_text}\"? → {'✓' if result else '✗'}"
        )
        return result

    
    m = CONDITION_ANY_TEXT.match(condition_str)
    if m:
        target_text = m.group("text").lower().strip()
        for i, line in enumerate(lines, 1):
            if target_text in line.lower():
                logger.info(
                    f"[CONDITION] ✓ Found \"{target_text}\" in line {i}: "
                    f"\"{line[:50]}\""
                )
                return True
        logger.info(f"[CONDITION] ✗ \"{target_text}\" not found in any line")
        return False

    
    m = CONDITION_ANY_COMPARE.match(condition_str)
    if m:
        op = m.group("op")
        for i, line in enumerate(lines, 1):
            numbers = extract_first_numbers(line, count=2)
            if len(numbers) >= 2:
                x, y = numbers[0], numbers[1]
                if compare_numbers(x, y, op):
                    logger.info(
                        f"[CONDITION] ✓ Line {i} matches {x} {op} {y}: "
                        f"\"{line[:50]}\""
                    )
                    return True
        logger.info(f"[CONDITION] ✗ No line matches X {op} Y")
        return False

    
    m = CONDITION_LINE_TEXT.match(condition_str)
    if m:
        line_num = int(m.group("line"))
        target_text = m.group("text").lower().strip()

        line_content = get_line(message, line_num)
        if line_content is None:
            logger.info(
                f"[CONDITION] ✗ Line {line_num} doesn't exist "
                f"(msg has {len(lines)} lines)"
            )
            return False

        result = target_text in line_content.lower()
        logger.info(
            f"[CONDITION] line{line_num} contains \"{target_text}\"? "
            f"→ {'✓' if result else '✗'} "
            f"(line: \"{line_content[:50]}\")"
        )
        return result

    
    m = CONDITION_LINE_COMPARE.match(condition_str)
    if m:
        line_num = int(m.group("line"))
        op = m.group("op")

        line_content = get_line(message, line_num)
        if line_content is None:
            logger.info(
                f"[CONDITION] ✗ Line {line_num} doesn't exist "
                f"(msg has {len(lines)} lines)"
            )
            return False

        numbers = extract_first_numbers(line_content, count=2)
        if len(numbers) < 2:
            logger.info(
                f"[CONDITION] ✗ Line {line_num} doesn't have 2 numbers "
                f"(found: {numbers}, content: \"{line_content[:50]}\")"
            )
            return False

        x, y = numbers[0], numbers[1]
        result = compare_numbers(x, y, op)
        logger.info(
            f"[CONDITION] line{line_num}: {x} {op} {y}? "
            f"→ {'✓' if result else '✗'} "
            f"(line: \"{line_content[:50]}\")"
        )
        return result

    logger.warning(f"[CONDITION] Unknown condition format: '{condition_str}'")
    return False






async def wait_for_bot_reply(
    client,
    chat_id: int,
    session_name: str,
    after_msg_id: int,
    timeout: Optional[int] = None,
):
    """Wait for a new message in the chat after `after_msg_id`."""
    if timeout is None:
        timeout = BUTTON_CLICK_TIMEOUT

    found_event = asyncio.Event()
    result = {"message": None}

    async def _on_new(event):
        if event.id <= after_msg_id:
            return
        if event.message.out:
            return
        if found_event.is_set():
            return
        result["message"] = event.message
        found_event.set()

    async def _on_edit(event):
        if event.id <= after_msg_id:
            return
        if event.message.out:
            return
        if found_event.is_set():
            return
        result["message"] = event.message
        found_event.set()

    handler_new = client.on(events.NewMessage(chats=chat_id))(_on_new)
    handler_edit = client.on(events.MessageEdited(chats=chat_id))(_on_edit)

    logger.info(
        f"[CONDITION] [{session_name}] Waiting for bot reply "
        f"(timeout {timeout}s, after msg {after_msg_id})…"
    )

    try:
        try:
            await asyncio.wait_for(found_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                f"[CONDITION] [{session_name}] ⏰ Timeout ({timeout}s) — no reply received"
            )
    finally:
        client.remove_event_handler(handler_new)
        client.remove_event_handler(handler_edit)

    if result["message"]:
        logger.info(
            f"[CONDITION] [{session_name}] Got reply (msg_id={result['message'].id})"
        )

    return result["message"]

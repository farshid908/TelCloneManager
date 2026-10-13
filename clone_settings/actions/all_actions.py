"""Clone membership listing and selective join/leave actions."""

__TCM_FILE_HASH__ = "4782764613"

import asyncio
import html
import logging
import re

from telethon.errors import (
    FloodWaitError,
    UserAlreadyParticipantError,
    UserNotParticipantError,
)
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    JoinChannelRequest,
    LeaveChannelRequest,
)
from telethon.tl.functions.messages import (
    DeleteChatUserRequest,
    GetFullChatRequest,
    ImportChatInviteRequest,
)
from telethon.tl.types import Channel, Chat

from ..utils.keyboards import make_inline_keyboard

PAGE_SIZE = 5
_reports = {}
logger = logging.getLogger("CloneManager")
_JOIN_LINK_RE = re.compile(
    r"https?://t\.me/(?:joinchat/|\+)[A-Za-z0-9_-]+|"
    r"https?://t\.me/[A-Za-z][A-Za-z0-9_]{3,}",
    re.IGNORECASE,
)


def _chat_key(entity):
    return type(entity).__name__, int(entity.id)


def _chat_id(entity):
    if isinstance(entity, Channel):
        return f"-100{entity.id}"
    return f"-{entity.id}"


def _clone_number(name, fallback):
    match = re.search(r"(\d+)$", str(name))
    return int(match.group(1)) if match else fallback


def _clone_records(automation):
    clients = list(getattr(automation, "clone_clients", []))
    names = list(getattr(automation, "clone_names", []))
    for position, client in enumerate(clients, start=1):
        name = names[position - 1] if position <= len(names) else f"Clone{position}"
        yield _clone_number(name, position), client, name


def _format_clone_numbers(numbers):
    values = sorted(set(numbers))
    if not values:
        return "None"
    result = []
    start = previous = values[0]
    for number in values[1:]:
        if number == previous + 1:
            previous = number
            continue
        result.append(
            f"Clone{start}-{previous}" if previous > start else f"Clone{start}"
        )
        start = previous = number
    result.append(f"{start}-{previous}" if previous > start else f"{previous}")
    first = result[0]
    return first + (" " + " ".join(result[1:]) if len(result) > 1 else "")


def _invite_hash(link):
    match = re.search(r"t\.me/(?:joinchat/|\+)([A-Za-z0-9_-]+)", link)
    return match.group(1) if match else None


def _join_link_from_about(about):
    for match in _JOIN_LINK_RE.findall(about or ""):
        if _invite_hash(match):
            return match
    return None


async def _find_join_link(client, entity):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}"
    try:
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        elif isinstance(entity, Chat):
            full = await client(GetFullChatRequest(entity.id))
        else:
            return None
        about = getattr(getattr(full, "full_chat", None), "about", "")
        return _join_link_from_about(about)
    except Exception:
        return None


async def scan_memberships(automation):
    memberships = {}
    for clone_index, client, _ in _clone_records(automation):
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not isinstance(entity, (Chat, Channel)):
                continue
            key = _chat_key(entity)
            item = memberships.setdefault(
                key,
                {
                    "entity": entity,
                    "client": client,
                    "title": getattr(entity, "title", str(entity.id)),
                    "clones": [],
                    "clone_entities": {},
                    "join_link": None,
                },
            )
            if clone_index not in item["clones"]:
                item["clones"].append(clone_index)
                item["clone_entities"][clone_index] = entity

    items = sorted(memberships.values(), key=lambda item: str(item["title"]).lower())
    for item in items:
        item["clones"].sort()
        item["join_link"] = await _find_join_link(item["client"], item["entity"])
    return items


def _render_item(item, number, total_clones):
    title = html.escape(str(item["title"]))
    if item.get("join_link"):
        title = f'<a href="{html.escape(item["join_link"], quote=True)}">{title}</a>'
    member_count = len(item["clones"])
    if total_clones > 0 and member_count == total_clones:
        membership = "All clone"
    else:
        membership = (
            f"{html.escape(_format_clone_numbers(item['clones']))} "
            f"({member_count} clones)"
        )
    return (
        f"{number}. {title} | <code>{html.escape(_chat_id(item['entity']))}</code> | "
        f"{membership}"
    )


def _report_keyboard(items, page, total_clones):
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    rows = []
    for offset, item in enumerate(items[start:start + PAGE_SIZE], start=1):
        number = start + offset
        row = [(str(number), "noop")]
        if item["clones"]:
            row.append(("Left🔻", f"all:left:{page}:{number - 1}"))
        if item.get("join_link") and len(item["clones"]) < total_clones:
            row.append(("join🔺", f"all:join:{page}:{number - 1}"))
        rows.append(row)
    navigation = []
    if page > 1:
        navigation.append(("Back", f"all:page:{page - 1}"))
    else:
        navigation.append(("Back", "menu:main"))
    navigation.append((f"{page}/{total_pages}", "noop"))
    if page < total_pages:
        navigation.append(("Next", f"all:page:{page + 1}"))
    rows.append(navigation)
    rows.append([("Main Menu〽️", "menu:main")])
    return make_inline_keyboard(rows)


def render_report(items, page, total_clones):
    if not items:
        text = (
            f"Total clones: {total_clones}\n"
            "Groups/channels and clone memberships:\n\n"
            "No groups or channels found."
        )
    else:
        start = (page - 1) * PAGE_SIZE
        visible = items[start:start + PAGE_SIZE]
        lines = [
            f"Total clones: {total_clones}",
            "Groups/channels and clone memberships:",
            "",
        ]
        body = "\n----------------------\n".join(
            _render_item(item, start + index + 1, total_clones)
            for index, item in enumerate(visible)
        )
        text = "\n".join(lines) + body
    return text, _report_keyboard(items, page, total_clones)


def _prompt_keyboard(action):
    label = "Join All clones💠" if action == "join" else "Left All clones🔻"
    return make_inline_keyboard([
        [(label, f"all:{action}_all")],
        [("Cancel🚫", "all:cancel")],
    ])


def _prompt_text(action, total):
    verb = "join" if action == "join" else "leave"
    return (
        f"Active clones: {total}\n"
        "Send clone number(s) in one of these formats:\n"
        "1\n"
        "1-5\n"
        "1-7 , 9 , 15\n"
        "These are clone account numbers, not group numbers.\n"
        f"To make every clone {verb} this group, tap the button below."
    )


async def _edit_callback(event, text, keyboard):
    query = getattr(event, "_query", None)
    message = getattr(query, "message", None) if query else None
    if message is not None:
        return await message.edit_text(
            text, reply_markup=keyboard, parse_mode="HTML"
        )
    return await event.edit(text, buttons=keyboard)


async def _answer_new_message(event, text, keyboard):
    answer = getattr(event, "answer", None)
    if callable(answer):
        return await answer(text, reply_markup=keyboard, parse_mode="HTML")

    message = getattr(event, "_message", None)
    answer = getattr(message, "answer", None)
    if callable(answer):
        return await answer(text, reply_markup=keyboard, parse_mode="HTML")

    reply = getattr(event, "reply", None)
    if callable(reply):
        return await reply(text, buttons=keyboard, parse_mode="html")
    raise TypeError("Could not send the clone membership report")


async def _edit_new_message(message, text, keyboard):
    edit_text = getattr(message, "edit_text", None)
    if callable(edit_text):
        return await edit_text(
            text, reply_markup=keyboard, parse_mode="HTML"
        )

    edit = getattr(message, "edit", None)
    if callable(edit):
        return await edit(text, buttons=keyboard, parse_mode="html")
    raise TypeError("Could not update the clone membership report")


async def _edit_pending_message(message, pending, text, keyboard):
    target = pending.get("target") or {}
    if target.get("chat_id") and target.get("message_id"):
        await message.bot.edit_message_text(
            chat_id=target["chat_id"],
            message_id=target["message_id"],
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


def _parse_selection(raw, available_numbers):
    normalized = (raw or "").replace(",", " ").replace("'", " ").replace(".", " ")
    available_numbers = set(available_numbers)
    selected = set()
    for token in normalized.split():
        if "-" in token:
            start, end = token.split("-", 1)
            if not start.isdigit() or not end.isdigit():
                raise ValueError("Use numbers and ranges such as 1-5 10 17.")
            selected.update(range(min(int(start), int(end)), max(int(start), int(end)) + 1))
        elif token.isdigit():
            selected.add(int(token))
        else:
            raise ValueError("Use numbers and ranges such as 1-5 10 17.")
    if not selected:
        raise ValueError("Enter at least one active clone number.")
    unavailable = sorted(selected - available_numbers)
    if unavailable:
        values = ", ".join(map(str, unavailable))
        raise ValueError(f"Clone number(s) are not active: {values}.")
    return sorted(selected)


async def _join_one(client, item):
    link = item.get("join_link")
    if not link:
        return "skipped"
    invite_hash = _invite_hash(link)
    try:
        if invite_hash:
            await client(ImportChatInviteRequest(invite_hash))
        else:
            await client(JoinChannelRequest(item["entity"]))
        return "success"
    except UserAlreadyParticipantError:
        return "already"
    except FloodWaitError as exc:
        await asyncio.sleep(exc.seconds + 1)
        return await _join_one(client, item)
    except Exception:
        return "failed"


async def _leave_one(client, item, entity=None, clone_index=None):
    for attempt in range(2):
        try:
            if entity is None:
                entity = await client.get_entity(_chat_id(item["entity"]))
            if isinstance(entity, Channel):
                await client(LeaveChannelRequest(entity))
                return "success"
            if isinstance(entity, Chat):
                me = await client.get_me()
                await client(DeleteChatUserRequest(chat_id=entity.id, user_id=me.id))
                return "success"
            return "failed"
        except UserNotParticipantError:
            return "already"
        except FloodWaitError as exc:
            if attempt:
                logger.warning(
                    "[MEMBERSHIP-LEAVE] Clone%s failed: FloodWaitError",
                    clone_index,
                )
                return "failed:FloodWaitError"
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            error_type = type(exc).__name__
            logger.warning(
                "[MEMBERSHIP-LEAVE] Clone%s failed: %s",
                clone_index,
                error_type,
            )
            return "failed:" + error_type
    return "failed:UnknownError"


async def _run_membership_action(
    message, pending, automation, action, indices, owner_id=None,
):
    if owner_id is None:
        owner_id = getattr(getattr(message, "from_user", None), "id", None)
    items = _reports.get(owner_id, [])
    item_index = pending["data"]["item_index"]
    if item_index >= len(items):
        await message.answer("The selected group is no longer available.")
        return
    item = items[item_index]
    clone_records = {
        number: (client, name)
        for number, client, name in _clone_records(automation)
    }
    if action == "left":
        current_members = set(item["clones"])
        indices = [index for index in indices if index in current_members]
    total = len(indices)
    results = {"success": 0, "already": 0, "failed": 0, "skipped": 0}
    failure_types = set()
    for completed, clone_index in enumerate(indices, start=1):
        if clone_index not in clone_records:
            results["failed"] += 1
            failure_types.add("CloneNotActive")
            continue
        client, name = clone_records[clone_index]
        status = "Joining" if action == "join" else "Leaving"
        percent = int(completed * 100 / total)
        bar = "█" * (percent // 10) + "░" * (10 - percent // 10)
        await _edit_pending_message(
            message,
            pending,
            f"{status} {name}...\n[{bar}] {percent}%",
            None,
        )
        try:
            result = (
                await _join_one(client, item)
                if action == "join"
                else await _leave_one(
                    client,
                    item,
                    item.get("clone_entities", {}).get(clone_index),
                    clone_index,
                )
            )
            if result.startswith("failed:"):
                results["failed"] += 1
                failure_types.add(result.partition(":")[2])
            else:
                results[result] += 1
        except Exception as exc:
            results["failed"] += 1
            failure_types.add(type(exc).__name__)
    completion_text = f"{action.title()} completed.\n[{('█' * 10)}] 100%"
    if results["failed"]:
        details = ", ".join(sorted(failure_types))
        completion_text += f"\nFailed: {results['failed']} clone(s)."
        if details:
            completion_text += " (" + details + ")"
    await _edit_pending_message(message, pending, completion_text, None)
    checking_message = await _answer_new_message(
        message, "Checking clone memberships...", None
    )
    fresh_items = await scan_memberships(automation)
    _reports[owner_id] = fresh_items
    text, keyboard = render_report(fresh_items, 1, len(automation.clone_clients))
    await _edit_new_message(checking_message, text, keyboard)


async def handle_all_callback(event, data, automation, set_pending_input, clear_pending_input):
    user_id = event.sender_id
    total = len(getattr(automation, "clone_clients", []))
    if data == "menu:all":
        await event.edit("Checking clone memberships...", buttons=None)
        items = await scan_memberships(automation)
        _reports[user_id] = items
        text, keyboard = render_report(items, 1, total)
        await _edit_callback(event, text, keyboard)
        await event.answer()
        return
    if data.startswith("all:page:"):
        page = int(data.rsplit(":", 1)[1])
        items = _reports.get(user_id, [])
        text, keyboard = render_report(items, page, total)
        await _edit_callback(event, text, keyboard)
        await event.answer()
        return
    if data in {"all:join_all", "all:left_all"}:
        pending = __import__(
            "clone_settings.callback_handler", fromlist=["get_pending_input"]
        ).get_pending_input(user_id)
        if not pending:
            await event.answer("This action has expired.", alert=True)
            return
        action = "join" if data == "all:join_all" else "left"
        await event.answer()
        await _run_membership_action(
            event._query.message, pending, automation, action,
            sorted(number for number, _, _ in _clone_records(automation)),
            owner_id=user_id,
        )
        clear_pending_input(user_id)
        return
    if data == "all:cancel":
        pending = __import__(
            "clone_settings.callback_handler", fromlist=["get_pending_input"]
        ).get_pending_input(user_id)
        clear_pending_input(user_id)
        items = _reports.get(user_id, [])
        page = pending.get("data", {}).get("page", 1) if pending else 1
        text, keyboard = render_report(items, page, total)
        await _edit_callback(event, text, keyboard)
        await event.answer()
        return
    match = re.fullmatch(r"all:(join|left):(\d+):(\d+)", data)
    if match:
        action, page, item_index = match.group(1), int(match.group(2)), int(match.group(3))
        items = _reports.get(user_id, [])
        if item_index >= len(items):
            await event.answer("The selected group is no longer available.", alert=True)
            return
        item = items[item_index]
        if action == "join" and not item.get("join_link"):
            await event.answer("No join link was found.", alert=True)
            return
        set_pending_input(
            user_id,
            f"all:{action}_selection",
            {"item_index": item_index, "page": page},
        )
        await _edit_callback(event, _prompt_text(action, total), _prompt_keyboard(action))
        await event.answer()
        return
    await event.answer("Unknown membership action", alert=True)


async def complete_membership_selection(message, pending, text, automation):
    action = pending["type"].split(":", 2)[1].replace("_selection", "")
    total = len(getattr(automation, "clone_clients", []))
    try:
        available_numbers = {
            number for number, _, _ in _clone_records(automation)
        }
        indices = _parse_selection(text, available_numbers)
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await _run_membership_action(
        message,
        pending,
        automation,
        action,
        indices,
        owner_id=pending.get("_owner_id"),
    )

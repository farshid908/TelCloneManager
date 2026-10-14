"""Private bot chat interactions for user accounts."""

__TCM_FILE_HASH__ = "3393700504"


import asyncio
import logging
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from telethon import events
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import StartBotRequest
from telethon.tl.types import InputPeerUser, InputUser, User

from progress_bar import build_progress_bar

logger = logging.getLogger("TG-Auto")
BOT_START_COMMAND = re.compile(
    r"^/start(?:@\w+)?\s+(.+?)\s*$",
    re.IGNORECASE,
)
BOT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
START_PAYLOAD_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def parse_bot_start_link(link):
    raw_link = (link or "").strip()
    if not raw_link:
        raise ValueError("A bot start link is required.")
    if "://" not in raw_link:
        raw_link = "https://" + raw_link.lstrip("/")

    parsed = urlsplit(raw_link)
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.scheme.lower() == "tg" and parsed.netloc.lower() == "resolve":
        username = (parameters.get("domain") or [""])[0]
    elif parsed.scheme.lower() in {"http", "https"}:
        hostname = (parsed.hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if hostname not in {"t.me", "telegram.me"}:
            raise ValueError("Use a Telegram bot start link.")
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) != 1:
            raise ValueError("The link must open a bot's private chat.")
        username = path_parts[0]
    else:
        raise ValueError("Use an https://t.me bot start link.")

    payloads = parameters.get("start") or []
    if len(payloads) != 1:
        raise ValueError("The link must contain one start parameter.")
    payload = payloads[0]
    if not BOT_USERNAME_RE.fullmatch(username) or not username.lower().endswith("bot"):
        raise ValueError("The Telegram link does not contain a bot username.")
    if not START_PAYLOAD_RE.fullmatch(payload):
        raise ValueError("The bot start parameter is invalid.")
    return username, payload


async def start_bot_in_private_chat(client, username, payload):
    entity = await client.get_entity(username)
    if not isinstance(entity, User) or not entity.bot:
        raise ValueError("The Telegram account did not resolve to a bot.")
    access_hash = getattr(entity, "access_hash", None)
    if access_hash is None:
        raise ValueError("The bot access information is unavailable.")

    bot = InputUser(entity.id, access_hash)
    peer = InputPeerUser(entity.id, access_hash)
    return await client(
        StartBotRequest(
            bot=bot,
            peer=peer,
            start_param=payload,
            random_id=secrets.randbits(63),
        )
    )


async def _edit_status(status, text):
    try:
        await status.edit_text(text)
    except Exception:
        logger.debug("[BOT-CHAT] Status edit failed", exc_info=True)


async def _run_start_for_clones(automation, username, payload, status):
    clients = list(getattr(automation, "clone_clients", []))
    names = list(getattr(automation, "clone_names", []))
    total = len(clients)
    if not total:
        await _edit_status(status, "No active clone accounts.")
        return

    started = 0
    failed = []
    floodwait = []
    for index, client in enumerate(clients, start=1):
        name = names[index - 1] if index <= len(names) else f"Clone{index}"
        try:
            await start_bot_in_private_chat(client, username, payload)
            started += 1
            marker = "✅"
        except FloodWaitError as exc:
            floodwait.append(f"{name} ({exc.seconds}s)")
            marker = "⏳"
            logger.warning(
                "[BOT-CHAT] %s FloodWait %ss while starting @%s",
                name,
                exc.seconds,
                username,
            )
        except Exception as exc:
            failed.append(f"{name} ({type(exc).__name__})")
            marker = "❌"
            logger.warning(
                "[BOT-CHAT] %s failed to start @%s: %s",
                name,
                username,
                type(exc).__name__,
            )

        progress = build_progress_bar(index, total)
        await _edit_status(
            status,
            f"Starting @{username} with clones...\n{name}...{marker}\n"
            f"{progress}",
        )
        if index < total:
            await asyncio.sleep(0.5)

    summary = (
        f"Bot start completed.\nStarted: {started} | Failed: {len(failed)} | "
        f"FloodWait: {len(floodwait)}\n{build_progress_bar(total, total)}"
    )
    if failed:
        summary += "\nFailed: " + ", ".join(failed)
    if floodwait:
        summary += "\nFloodWait: " + ", ".join(floodwait)
    await _edit_status(status, summary)


def register_bot_interaction_handler(automation):
    admin_id = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=BOT_START_COMMAND,
        from_users=admin_id,
    ))
    async def _start_bot(event):
        if not automation.is_admin(event):
            return

        match = BOT_START_COMMAND.match((event.raw_text or "").strip())
        if not match:
            return
        try:
            username, payload = parse_bot_start_link(match.group(1))
        except ValueError as exc:
            await event.reply(
                f"Invalid bot link: {exc}\n"
                "Example: /start https://t.me/ExampleBot?start=payload"
            )
            return

        from clone_settings.bot_core import get_bot_client

        bot = get_bot_client()
        if bot is None or admin_id is None:
            await event.reply("The aiogram status bot is not available.")
            return

        total = len(getattr(automation, "clone_clients", []))
        initial = (
            f"Starting @{username} with {total} clone(s)...\n"
            f"{build_progress_bar(0, total or 1)}"
        )
        try:
            status = await bot.send_message(admin_id, initial)
        except Exception:
            logger.warning("[BOT-CHAT] Could not send aiogram status", exc_info=True)
            await event.reply("Could not send the aiogram progress message.")
            return

        await _run_start_for_clones(automation, username, payload, status)

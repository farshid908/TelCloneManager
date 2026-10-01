"""Vote for a matching option in a Telegram poll."""

import logging
import re

from telethon import events
from telethon.tl.functions.messages import SendVoteRequest
from telethon.tl.types import MessageMediaPoll

logger = logging.getLogger("TG-Auto")
LINK_RE = re.compile(
    r"(?:https?://)?t\.me/"
    r"(?:c/(?P<chat>\d+)|(?P<username>[A-Za-z0-9_]+))/"
    r"(?P<msg>\d+)",
    re.IGNORECASE,
)


async def _resolve_message(client, text, event):
    match = LINK_RE.search(text or "")
    if match:
        target = (
            int(f"-100{match.group('chat')}")
            if match.group("chat")
            else match.group("username")
        )
        try:
            entity = await client.get_entity(target)
            return await client.get_messages(
                entity, ids=int(match.group("msg"))
            )
        except Exception:
            return None
    if event.is_reply:
        return await event.get_reply_message()
    return None


async def _message_for_client(client, source, main_client):
    if client is main_client:
        return source
    try:
        entity = await client.get_entity(source.peer_id)
        return await client.get_messages(entity, ids=source.id)
    except Exception:
        return None


def register_pool_handler(automation):
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"^pool\s+.+$",
        from_users=aid,
    ))
    async def _pool(event):
        raw = event.raw_text.strip()[4:].strip()
        link_match = LINK_RE.search(raw)
        option_text = raw[:link_match.start()] if link_match else raw
        option_text = option_text.strip("\"' ")
        source = await _resolve_message(
            automation.main_client, raw, event
        )
        if not source or not isinstance(source.media, MessageMediaPoll):
            await event.reply("Target message is not a poll.", parse_mode=None)
            return

        answers = getattr(source.media.poll, "answers", [])
        selected = next(
            (
                answer for answer in answers
                if option_text.casefold() in
                (answer.text or "").casefold()
            ),
            None,
        )
        if selected is None:
            await event.reply(
                "No poll option matched the requested text.",
                parse_mode=None,
            )
            return

        clients = [automation.main_client, *automation.clone_clients]
        success = 0
        for client in clients:
            try:
                message = await _message_for_client(
                    client, source, automation.main_client
                )
                if not message or not isinstance(
                    message.media, MessageMediaPoll
                ):
                    continue
                entity = await client.get_entity(message.peer_id)
                await client(SendVoteRequest(
                    peer=entity,
                    msg_id=message.id,
                    options=[selected.option],
                ))
                success += 1
            except Exception as exc:
                logger.warning("[POOL] Vote failed: %s", exc)

        await event.edit(
            f"Poll option '{selected.text}' selected for "
            f"{success}/{len(clients)} accounts.",
            parse_mode=None,
        )

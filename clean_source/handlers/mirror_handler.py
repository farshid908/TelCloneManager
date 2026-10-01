import asyncio
import logging

from telethon import TelegramClient, events

from config import BRIDGE_GROUP, AUTO_DELETE_DELAY
from entity_resolver import ChatTarget, auto_extract_chat_target, resolve_entity
from message_sender import safe_send_message

logger = logging.getLogger("TG-Auto")


def _has_media(message) -> bool:
    """Check if a message contains any type of media."""
    if message.gif:
        return True
    if message.video:
        return True
    if message.audio:
        return True
    if message.voice:
        return True
    if message.video_note:
        return True
    if message.sticker:
        return True
    if message.photo:
        return True
    if message.document:
        return True
    return False


def _get_media_type_label(message) -> str:
    """Get a human-readable label for the media type."""
    if message.gif:
        return "gif"
    if message.sticker:
        return "sticker"
    if message.video_note:
        return "video_note"
    if message.video:
        return "video"
    if message.voice:
        return "voice"
    if message.audio:
        return "audio"
    if message.photo:
        return "photo"
    if message.document:
        return "file"
    return "unknown"


async def _mirror_send_file(
    client: TelegramClient,
    chat_target: ChatTarget,
    original_message,
    session_name: str,
    reply_to=None,
) -> bool:
    """Download media from original message and re-send via clone."""
    entity = await resolve_entity(
        client, chat_target.send_target, session_name=session_name,
    )
    if entity is None:
        logger.error(f"[MIRROR] [{session_name}] ✗ Entity resolution failed")
        return False

    try:
        media_bytes = await original_message.download_media(file=bytes)
        if media_bytes is None:
            logger.error(
                f"[MIRROR] [{session_name}] ✗ Media download returned None"
            )
            return False

        kwargs = {}
        if reply_to:
            kwargs["reply_to"] = reply_to

        caption = original_message.text or ""

        if original_message.voice:
            kwargs["voice_note"] = True
        if original_message.video_note:
            kwargs["video_note"] = True

        await client.send_file(entity, media_bytes, caption=caption, **kwargs)
        logger.info(
            f"[MIRROR] [{session_name}] ✓ Media mirrored to "
            f"'{chat_target.display_name}'"
        )
        return True
    except Exception as e:
        logger.error(
            f"[MIRROR] [{session_name}] ✗ Mirror media failed: "
            f"{type(e).__name__}: {e}"
        )
        return False


async def _auto_delete_messages(client, chat_id, msg_ids, delay):
    """Wait then delete messages."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
        logger.info(f"[DELETE] 🗑️ Auto-deleted {len(msg_ids)} message(s)")
    except Exception as e:
        logger.warning(f"[DELETE] Failed to delete: {e}")


def register_mirror_handler(automation):
    """Register mirror on/off with auto-delete, and media mirroring."""
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r'^mirror\s+on$', from_users=aid,
    ))
    async def _mirror_on(event):
        if not automation.is_admin(event):
            return

        automation.mirror_mode = True
        reply_msg = await event.reply("✅ **mirror mod on**")
        logger.info("[MIRROR] ✓ Activated")

        
        msgs_to_delete = [event.id]
        if reply_msg:
            msgs_to_delete.append(reply_msg.id)

        asyncio.create_task(
            _auto_delete_messages(
                automation.main_client,
                event.chat_id,
                msgs_to_delete,
                AUTO_DELETE_DELAY,
            )
        )

    @automation.main_client.on(events.NewMessage(
        pattern=r'^mirror\s+off$', from_users=aid,
    ))
    async def _mirror_off(event):
        if not automation.is_admin(event):
            return

        automation.mirror_mode = False
        reply_msg = await event.reply("❌ **mirror mod off**")
        logger.info("[MIRROR] ✗ Deactivated")

        
        msgs_to_delete = [event.id]
        if reply_msg:
            msgs_to_delete.append(reply_msg.id)

        asyncio.create_task(
            _auto_delete_messages(
                automation.main_client,
                event.chat_id,
                msgs_to_delete,
                AUTO_DELETE_DELAY,
            )
        )

    @automation.main_client.on(events.NewMessage(outgoing=True))
    async def _mirror_replicate(event):
        if not automation.mirror_mode or not automation.is_admin(event):
            return

        raw = (event.raw_text or "").strip().lower()
        skip_prefixes = (
            "/go", "send ", "mirror ", "stop", "/status",
        )
        if any(raw.startswith(p) for p in skip_prefixes):
            return

        chat_target = await auto_extract_chat_target(
            automation.main_client, event,
            automation.clone_clients, automation.clone_names,
            BRIDGE_GROUP,
        )

        reply_to_id = event.reply_to_msg_id
        has_media = _has_media(event.message)

        if has_media:
            
            media_label = _get_media_type_label(event.message)
            logger.info(
                f"[MIRROR] Mirroring {media_label} to "
                f"{len(automation.clone_clients)} clone(s)"
            )

            tasks = [
                _mirror_send_file(
                    c, chat_target, event.message,
                    session_name=n, reply_to=reply_to_id,
                )
                for c, n in zip(
                    automation.clone_clients, automation.clone_names
                )
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ok = sum(1 for r in results if r is True)
            logger.info(
                f"[MIRROR] Media batch: ✓{ok} ✗{len(results) - ok}"
            )

        elif event.raw_text:
            
            logger.info(
                f"[MIRROR] Mirroring text to "
                f"{len(automation.clone_clients)} clone(s)"
            )

            tasks = [
                safe_send_message(
                    c, chat_target, event.raw_text,
                    session_name=n, reply_to=reply_to_id,
                )
                for c, n in zip(
                    automation.clone_clients, automation.clone_names
                )
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            ok = sum(1 for r in results if r is True)
            logger.info(
                f"[MIRROR] Text batch: ✓{ok} ✗{len(results) - ok}"
            )

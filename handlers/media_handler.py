"""
Handler for media commands: save as, send, del, list (vim/vom/mus/vid).
"""

import asyncio
import logging
from telethon import events

from config import AUTO_DELETE_DELAY
from command_parser import (
    parse_save_media_command,
    parse_send_media_command,
    parse_del_media_command,
    parse_list_media_command,
)
from media_manager import (
    save_media_from_message,
    send_media,
    delete_media,
    list_media,
    MEDIA_TYPES,
)

logger = logging.getLogger("TG-Auto")


async def _auto_delete(client, chat_id, msg_ids, delay):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
    except Exception:
        pass


def register_media_handler(automation):
    """Register save/send/del/list media handlers."""
    aid = automation._admin_user_id

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^save\s+as\s+(vim|vom|mus|vid)\s""", from_users=aid,
    ))
    async def _save_media(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_save_media_command(event.raw_text)
            if not parsed:
                await event.reply(
                    "⚠️ Usage: reply to a media message with:\n"
                    '`save as vim "name"` — Video Message\n'
                    '`save as vom "name"` — Voice Message\n'
                    '`save as mus "name"` — Music\n'
                    '`save as vid "name"` — Video'
                )
                return

            if not event.is_reply:
                await event.reply(
                    "⚠️ Reply to a media message (voice/video/music/file)"
                )
                return

            reply_msg = await event.get_reply_message()
            if not reply_msg or not reply_msg.media:
                await event.reply("⚠️ Replied message has no media")
                return

            media_type = parsed["media_type"]
            name = parsed["name"]
            type_info = MEDIA_TYPES.get(media_type, {})

            status = await event.reply(
                f"⏳ Saving {type_info.get('emoji', '📎')} "
                f"{type_info.get('label', media_type)} as **\"{name}\"**…"
            )

            result = await save_media_from_message(
                client=automation.main_client,
                message=reply_msg,
                media_type=media_type,
                name=name,
                session_name="Main",
            )

            if result["success"]:
                await status.edit(
                    f"✅ {result['message']}\n\n"
                    f"Send with: `send {media_type} \"{name}\"`"
                )
            else:
                await status.edit(f"❌ {result['message']}")

        except Exception as e:
            logger.error(f"[SAVE-MEDIA] Error: {e}", exc_info=True)

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^send(?:\([^)]*\))?\s+(vim|vom|mus|vid)\s""",
        from_users=aid,
    ))
    async def _send_media(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_send_media_command(event.raw_text)
            if not parsed:
                return

            media_type = parsed["media_type"]
            name = parsed["name"]
            clone_indices = parsed["clone_indices"]
            include_main = parsed["include_main"]
            no_trace = parsed["no_trace"]

            chat_id = event.chat_id
            type_info = MEDIA_TYPES.get(media_type, {})

            
            senders = []

            if clone_indices is None:
                
                if include_main:
                    senders.append((automation.main_client, "Main"))

            elif clone_indices == "all":
                
                if include_main:
                    senders.append((automation.main_client, "Main"))
                for c, n in zip(
                    automation.clone_clients, automation.clone_names
                ):
                    senders.append((c, n))

            else:
                
                if include_main:
                    senders.append((automation.main_client, "Main"))
                for idx in clone_indices:
                    zero_idx = idx - 1
                    if 0 <= zero_idx < len(automation.clone_clients):
                        senders.append((
                            automation.clone_clients[zero_idx],
                            automation.clone_names[zero_idx],
                        ))

            if not senders:
                await event.reply("⚠️ No sessions selected")
                return

            
            sender_desc = ", ".join(n for _, n in senders)
            logger.info(
                f"[SEND-MEDIA] {type_info.get('emoji', '📎')} "
                f"'{name}' → [{sender_desc}] in chat {chat_id}"
            )

            
            success = 0
            failed = 0

            for client, session_name in senders:
                try:
                    ok = await send_media(
                        client=client,
                        chat_id=chat_id,
                        media_type=media_type,
                        name=name,
                        session_name=session_name,
                    )
                    if ok:
                        success += 1
                    else:
                        failed += 1
                except Exception as e:
                    logger.error(
                        f"[SEND-MEDIA] [{session_name}] Error: {e}"
                    )
                    failed += 1

                
                await asyncio.sleep(0.5)

            logger.info(
                f"[SEND-MEDIA] Complete: ✓{success} ✗{failed}"
            )

            
            if no_trace:
                asyncio.create_task(
                    _auto_delete(
                        automation.main_client, event.chat_id,
                        [event.id], AUTO_DELETE_DELAY,
                    )
                )

        except Exception as e:
            logger.error(f"[SEND-MEDIA] Error: {e}", exc_info=True)

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^del\s+(vim|vom|mus|vid)\s""", from_users=aid,
    ))
    async def _del_media(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_del_media_command(event.raw_text)
            if not parsed:
                return

            result = delete_media(parsed["media_type"], parsed["name"])

            if result["success"]:
                await event.reply(f"🗑️ {result['message']}")
            else:
                await event.reply(f"⚠️ {result['message']}")

        except Exception as e:
            logger.error(f"[DEL-MEDIA] Error: {e}", exc_info=True)

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^list\s+(vim|vom|mus|vid|all)\s*$""",
        from_users=aid,
    ))
    async def _list_media(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_list_media_command(event.raw_text)
            if not parsed:
                return

            items = list_media(parsed["media_type"])

            if not items:
                type_desc = (
                    parsed["media_type"] or "all"
                ).upper()
                await event.reply(f"ℹ️ No saved {type_desc} media")
                return

            lines = ["📁 **Saved Media:**\n"]

            for item in items:
                lines.append(
                    f"  {item['emoji']} `{item['name']}` — "
                    f"{item['label']} ({item['size_kb']} KB)"
                )

            lines.append(f"\nTotal: **{len(items)}** item(s)")
            await event.reply("\n".join(lines))

        except Exception as e:
            logger.error(f"[LIST-MEDIA] Error: {e}", exc_info=True)

"""
Handler for privacy commands:
  show prof / hide prof  — applies to Main + ALL clones
  prof status            — show current privacy settings
  prof hideall           — force base rule to Nobody
"""

__TCM_FILE_HASH__ = "5226263148"


import asyncio
import logging

from telethon import events
from telethon.tl.types import User, Chat, Channel

from command_parser import (
    parse_show_prof_command,
    parse_prof_status_command,
    parse_prof_hideall_command,
)
from privacy_engine import (
    show_to_targets,
    hide_from_targets,
    force_hide_all,
    get_privacy_status_readable,
)

logger = logging.getLogger("TG-Auto")

PRIVACY_AUTO_DELETE_DELAY = 3.0


def _privacy_progress_bar(done, total, width=12):
    if total <= 0:
        return f"{'░' * width} 0%"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    return f"{'█' * filled}{'░' * (width - filled)} {int(ratio * 100)}%"


async def _create_privacy_status(automation, action, total):
    """Create a live status message in the Aiogram admin PV."""
    try:
        from clone_settings.bot_core import get_bot_client

        bot = get_bot_client()
        admin_id = getattr(automation, "_admin_user_id", None)
        if bot is None or admin_id is None:
            return None, None

        verb = "Showing" if action == "show" else "Hiding"
        status = await bot.send_message(
            admin_id,
            f"📸 {verb} profile privacy...\n"
            f"In progress\n"
            f"{_privacy_progress_bar(0, total)}",
        )

        async def update(name, marker, done):
            try:
                await status.edit_text(
                    f"📸 {verb} profile privacy...\n"
                    f"{name}...{marker}\n"
                    f"{_privacy_progress_bar(done, total)}"
                )
            except Exception as exc:
                logger.debug("[PRIVACY] Status edit skipped: %s", exc)

        return status, update
    except Exception as exc:
        logger.warning("[PRIVACY] Could not create Aiogram status: %s", exc)
        return None, None


async def _auto_delete_after(client, chat_id, msg_ids, delay):
    """Wait then delete messages."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
        logger.info(f"[PRIVACY] 🗑️ Auto-deleted {len(msg_ids)} message(s)")
    except Exception as e:
        logger.warning(f"[PRIVACY] Auto-delete failed: {e}")


async def _resolve_usernames_to_ids(client, usernames, session_name):
    """Convert list of @usernames to list of user IDs."""
    user_ids = []
    failed = []

    for username in usernames:
        uname = username.lstrip("@")
        try:
            entity = await client.get_entity(uname)
            if isinstance(entity, User):
                user_ids.append(entity.id)
            else:
                logger.warning(
                    f"[PRIVACY] [{session_name}] @{uname} is not a user"
                )
                failed.append(uname)
        except Exception as e:
            logger.error(
                f"[PRIVACY] [{session_name}] Can't resolve @{uname}: {e}"
            )
            failed.append(uname)

    return user_ids, failed


def _privacy_chat_target_id(chat):
    """
    Convert a chat/channel entity to the positive raw ID expected by
    privacy chat-participant rules.

    In most Telethon chat objects, `chat.id` is already the raw positive ID.
    This helper also tolerates marked negative IDs just in case.
    """
    raw_id = int(getattr(chat, "id"))

    if raw_id >= 0:
        return raw_id

    raw_id = abs(raw_id)
    raw_str = str(raw_id)

    
    if raw_str.startswith("100") and len(raw_str) > 3:
        try:
            return int(raw_str[3:])
        except ValueError:
            pass

    return raw_id


def _chat_id_from_c_link(value):
    """Extract raw channel ID from a t.me/c/<id>/<message> link."""
    import re
    match = re.search(
        r"(?:https?://)?t\.me/c/(\d+)(?:/\d+)?",
        value,
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


async def _resolve_privacy_targets(client, raw_targets, session_name):
    """Resolve usernames, public links, user IDs, and chat IDs."""
    user_ids = []
    chat_ids = []
    failed = []

    for raw in raw_targets:
        value = str(raw).strip().strip("[]()<>.,")
        if not value:
            continue

        c_link_id = _chat_id_from_c_link(value)
        if c_link_id is not None:
            chat_ids.append(c_link_id)
            continue

        lookup = value
        if value.lower().startswith("t.me/"):
            lookup = f"https://{value}"
        if value.lower().startswith(("http://t.me/", "https://t.me/")):
            import re
            public_match = re.match(
                r"https?://t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})(?:/\d+)?$",
                value,
                re.IGNORECASE,
            )
            if public_match:
                lookup = public_match.group(1)
        elif value.startswith("@"):
            lookup = value[1:]
        elif value.isdigit() or (
            value.startswith("-") and value[1:].isdigit()
        ):
            lookup = int(value)

        try:
            entity = await client.get_entity(lookup)
        except Exception as exc:
            if isinstance(lookup, int) and str(lookup).startswith("-100"):
                chat_ids.append(abs(lookup) - 1000000000000)
                continue
            logger.error(
                f"[PRIVACY] [{session_name}] Can't resolve {value!r}: {exc}"
            )
            failed.append(value)
            continue

        if isinstance(entity, User):
            user_ids.append(entity.id)
        elif isinstance(entity, (Chat, Channel)):
            chat_ids.append(_privacy_chat_target_id(entity))
        else:
            failed.append(value)

    return user_ids, chat_ids, failed


async def _target_metadata(client, user_ids, chat_ids):
    metadata = {}
    for value, kind in [
        *((item, "user") for item in user_ids),
        *((item, "chat") for item in chat_ids),
    ]:
        try:
            entity = await client.get_entity(value)
            metadata[(kind, int(value))] = {
                "name": (
                    getattr(entity, "title", None)
                    or getattr(entity, "first_name", None)
                    or str(value)
                ),
                "username": getattr(entity, "username", None) or "",
            }
        except Exception:
            metadata[(kind, int(value))] = {
                "name": str(value),
                "username": "",
            }
    return metadata


async def _update_profile_related_privacy(
    client,
    action,
    user_ids,
    chat_ids,
    session_name,
    include_last_seen=False,
):
    """Apply photo visibility and related Saved Music policy."""
    update = show_to_targets if action == "show" else hide_from_targets
    results = []
    for key_name in ("photo", "savedmusic"):
        results.append(await update(
            client=client,
            key_name=key_name,
            user_ids=user_ids,
            chat_ids=chat_ids,
            session_name=session_name,
        ))
    if include_last_seen:
        results.append(await update(
            client=client,
            key_name="lastseen",
            user_ids=user_ids,
            chat_ids=chat_ids,
            session_name=session_name,
        ))
    return all(results)


def register_privacy_handler(automation):
    """Register all privacy-related command handlers."""
    aid = automation._admin_user_id

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^(show|hide)\s+prof""", from_users=aid,
    ))
    async def _prof(event):
        status = None
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_show_prof_command(event.raw_text)
            if not parsed:
                return

            action = parsed["action"]
            scope = parsed.get("scope", "all")
            raw_targets = parsed.get("targets", [])

            client = automation.main_client
            session_name = "Main"

            
            try:
                await event.delete()
            except Exception as exc:
                logger.debug("[PRIVACY-CMD] Could not delete command: %s", exc)

            total = 1
            if scope != "main":
                total += len(getattr(automation, "clone_names", []))
            status, update_status = await _create_privacy_status(
                automation, action, total
            )

            
            try:
                main_me = await client.get_me()
                main_user_id = main_me.id
            except Exception as e:
                logger.error(f"[PRIVACY-CMD] Can't get Main user_id: {e}")
                return

            is_saved_messages = (
                event.chat_id == main_user_id
                or (
                    event.is_private
                    and getattr(event.sender, "id", None) == main_user_id
                    and getattr(event.chat, "id", None) == main_user_id
                )
            )

            target_user_ids = []
            target_chat_ids = []
            target_desc = ""

            if raw_targets:
                
                logger.info(
                    f"[PRIVACY-CMD] Resolving {len(raw_targets)} target(s)…"
                )
                (
                    target_user_ids,
                    target_chat_ids,
                    failed,
                ) = await _resolve_privacy_targets(
                    client, raw_targets, session_name,
                )
                target_desc = (
                    f"{len(target_user_ids)} user(s), "
                    f"{len(target_chat_ids)} chat(s)"
                )
                if target_chat_ids:
                    target_desc += (
                        f" | CHATID: {', '.join(str(value) for value in target_chat_ids)}"
                    )
                if failed:
                    target_desc += f" ({len(failed)} failed)"

            else:
                
                if is_saved_messages:
                    
                    
                    
                    target_user_ids = [main_user_id]
                    target_desc = f"self (CHATID: {main_user_id})"
                    logger.info(
                        "[PRIVACY-CMD] Context: Saved Messages → Main self "
                        f"{main_user_id}"
                    )

                try:
                    chat = await event.get_chat()
                except Exception as e:
                    logger.error(f"[PRIVACY-CMD] Can't get chat: {e}")
                    return

                if isinstance(chat, User):
                    target_user_ids = [chat.id]
                    target_desc = f"user @{chat.username or chat.id}"
                    logger.info(
                        f"[PRIVACY-CMD] Context: private chat with user "
                        f"{chat.id}"
                    )

                elif isinstance(chat, (Chat, Channel)):
                    privacy_chat_id = _privacy_chat_target_id(chat)
                    chat_name = getattr(chat, "title", str(getattr(chat, "id", "?")))
                    target_chat_ids = [privacy_chat_id]
                    target_desc = (
                        f"chat '{chat_name}' "
                        f"(CHATID: {getattr(chat, 'id', privacy_chat_id)})"
                    )
                    logger.info(
                        f"[PRIVACY-CMD] Context: group/channel "
                        f"entity_id={getattr(chat, 'id', '?')} "
                        f"→ privacy_id={privacy_chat_id}"
                    )

                else:
                    logger.error(
                        f"[PRIVACY-CMD] Unknown chat type: "
                        f"{type(chat).__name__}"
                    )
                    return

            if not target_user_ids and not target_chat_ids:
                if status is not None:
                    try:
                        await status.edit_text("⚠️ No valid targets found")
                    except Exception:
                        pass
                return

            from privacy_targets import (
                forget_targets,
                remember_targets,
            )
            metadata = await _target_metadata(
                client, target_user_ids, target_chat_ids
            )
            if action == "show":
                remember_targets(
                    target_user_ids,
                    target_chat_ids,
                    metadata,
                )
            else:
                forget_targets(target_user_ids, target_chat_ids)

            
            main_ok = await _update_profile_related_privacy(
                client=client,
                action=action,
                user_ids=target_user_ids,
                chat_ids=target_chat_ids,
                session_name="Main",
            )
            if action == "show" and main_ok:
                from privacy_apply_state import mark_main_photo_targets
                mark_main_photo_targets(target_user_ids, target_chat_ids)
            if update_status is not None:
                await update_status("Main", "✅" if main_ok else "❌", 1)

            
            clone_success = 0
            clone_failed = 0

            clone_pairs = zip(
                automation.clone_clients,
                automation.clone_names,
            ) if scope != "main" else []

            for clone_client, clone_name in clone_pairs:
                try:
                    
                    ok = await _update_profile_related_privacy(
                        client=clone_client,
                        action=action,
                        user_ids=target_user_ids,
                        chat_ids=target_chat_ids,
                        session_name=clone_name,
                        include_last_seen=True,
                    )

                    if ok:
                        clone_success += 1
                    else:
                        clone_failed += 1

                    if update_status is not None:
                        await update_status(
                            clone_name,
                            "✅" if ok else "❌",
                            1 + clone_success + clone_failed,
                        )
                    await asyncio.sleep(0.3)

                except Exception as e:
                    logger.error(
                        f"[PRIVACY-CMD] [{clone_name}] Error: "
                        f"{type(e).__name__}: {e}"
                    )
                    clone_failed += 1
                    if update_status is not None:
                        await update_status(
                            clone_name,
                            "❌",
                            1 + clone_success + clone_failed,
                        )

            action_desc = (
                "✓ Now visible to" if action == "show"
                else "🚫 Hidden from"
            )

            logger.info(
                f"[PRIVACY-CMD] ✓ {action.upper()} photo → {target_desc} — "
                f"Main: {'✓' if main_ok else '✗'}, "
                f"Clones: ✓{clone_success} ✗{clone_failed}"
            )

            final_text = (
                f"{'👁' if action == 'show' else '🚫'} "
                f"**Applied**\n"
                f"Target: {target_desc}\n"
                f"Main: {'✓' if main_ok else '✗'}\n"
                f"Clones: ✓{clone_success} ✗{clone_failed}"
            )
            if status is not None:
                try:
                    await status.edit_text(final_text, parse_mode="Markdown")
                except Exception:
                    await status.edit_text(
                        final_text.replace("**", ""),
                        parse_mode=None,
                    )

        except Exception as e:
            logger.error(
                f"[PRIVACY-CMD] Handler error: {type(e).__name__}: {e}",
                exc_info=True,
            )
            if status is not None:
                try:
                    await status.edit_text(
                        f"⚠️ Profile privacy failed: {type(e).__name__}",
                        parse_mode=None,
                    )
                except Exception:
                    pass

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^prof\s+status""", from_users=aid,
    ))
    async def _prof_status(event):
        try:
            if not automation.is_admin(event):
                return

            if not parse_prof_status_command(event.raw_text):
                return

            client = automation.main_client
            info = await get_privacy_status_readable(
                client, "photo", "Main"
            )

            if "error" in info:
                await event.reply(f"⚠️ Error: {info['error']}")
                return

            base_desc = {
                "everyone": "🌐 Everyone",
                "nobody": "🔒 Nobody",
                "contacts": "👥 My Contacts",
                "unknown": "❓ Unknown",
            }.get(info["base_rule"], info["base_rule"])

            lines = [
                "📸 **Profile Photo Privacy Status**",
                "",
                f"**Base rule:** {base_desc}",
                "",
                f"✅ **Allowed:**",
                f"  • Users: {len(info['allowed_users'])}",
                f"  • Chats: {len(info['allowed_chats'])}",
            ]

            if info["allowed_users"]:
                lines.append("")
                lines.append("**Allowed user IDs:**")
                for uid in info["allowed_users"][:10]:
                    lines.append(f"  • `{uid}`")
                if len(info["allowed_users"]) > 10:
                    lines.append(
                        f"  ... and {len(info['allowed_users']) - 10} more"
                    )

            if info["allowed_chats"]:
                lines.append("")
                lines.append("**Allowed chat IDs:**")
                for chat_id in info["allowed_chats"][:10]:
                    lines.append(f"  • `{chat_id}`")
                if len(info["allowed_chats"]) > 10:
                    lines.append(
                        f"  ... and {len(info['allowed_chats']) - 10} more"
                    )

            if info["disallowed_users"] or info["disallowed_chats"]:
                lines.append("")
                lines.append("🚫 **Disallowed:**")
                lines.append(
                    f"  • Users: {len(info['disallowed_users'])}"
                )
                lines.append(
                    f"  • Chats: {len(info['disallowed_chats'])}"
                )

            await event.reply("\n".join(lines))

        except Exception as e:
            logger.error(
                f"[PRIVACY-STATUS] Error: {type(e).__name__}: {e}",
                exc_info=True,
            )
            try:
                await event.reply(f"⚠️ Error: {e}")
            except Exception:
                pass

    @automation.main_client.on(events.NewMessage(
        pattern=r"""^show\s+privacy\s*$""", from_users=aid,
    ))
    async def _show_privacy(event):
        try:
            if not automation.is_admin(event):
                return
            from privacy_targets import load_targets
            data = load_targets()
            lines = ["Allowed profile targets:", ""]
            for value, info in data["users"].items():
                name = info.get("name") or value
                username = info.get("username") or "_"
                if username and username != "_":
                    username = f"@{username.lstrip('@')}"
                lines.append(f"User {value} - {name} - {username}")
            for value, info in data["chats"].items():
                name = info.get("name") or value
                username = info.get("username") or "_"
                if username and username != "_":
                    username = f"@{username.lstrip('@')}"
                lines.append(f"Chat {value} - {name} - {username}")
            if len(lines) == 2:
                lines.append("No saved targets.")
            await event.reply("\n".join(lines), parse_mode=None)
        except Exception as exc:
            logger.error("[PRIVACY-LIST] Failed: %s", exc, exc_info=True)
            await event.reply(
                f"Privacy list failed: {type(exc).__name__}",
                parse_mode=None,
            )

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^prof\s+hideall""", from_users=aid,
    ))
    async def _prof_hideall(event):
        try:
            if not automation.is_admin(event):
                return

            if not parse_prof_hideall_command(event.raw_text):
                return

            client = automation.main_client

            success = await force_hide_all(
                client=client,
                key_name="photo",
                keep_whitelist=True,
                session_name="Main",
            )

            if success:
                await event.reply(
                    "🔒 **Profile photo forced to Nobody**\n"
                    "Only allowed users/chats can see it now.\n\n"
                    "Use `prof status` to verify."
                )
            else:
                await event.reply("⚠️ Failed to force hide")

        except Exception as e:
            logger.error(
                f"[PRIVACY-HIDEALL] Error: {type(e).__name__}: {e}",
                exc_info=True,
            )

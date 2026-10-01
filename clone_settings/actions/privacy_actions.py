"""
Privacy actions — change privacy settings for individual clones.

Supports:
  - Per-key privacy (photo, phone, lastseen, bio, forward, call, invite)
  - Per-value (everyone, contacts, nobody)
  - Bulk preset (all keys at once)
"""

import asyncio
import logging

from telethon.tl.functions.account import SetPrivacyRequest
from telethon.tl.types import (
    Channel,
    Chat,
    User,
    InputChannel,
    InputPeerChat,
    InputPeerChannel,
    InputUser,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyPhoneNumber,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyKeyChatInvite,
    InputPrivacyKeyPhoneCall,
    InputPrivacyKeyForwards,
    InputPrivacyKeyAbout,
    InputPrivacyKeySavedMusic,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowChatParticipants,
    InputPrivacyValueAllowContacts,
    InputPrivacyValueAllowUsers,
    InputPrivacyValueDisallowChatParticipants,
    InputPrivacyValueDisallowAll,
    InputPrivacyValueDisallowUsers,
)
from telethon.errors import FloodWaitError

from ..utils.keyboards import make_inline_keyboard

logger = logging.getLogger("CloneManager")






PRIVACY_KEY_MAP = {
    "photo":    InputPrivacyKeyProfilePhoto,
    "phone":    InputPrivacyKeyPhoneNumber,
    "lastseen": InputPrivacyKeyStatusTimestamp,
    "bio":      InputPrivacyKeyAbout,
    "forward":  InputPrivacyKeyForwards,
    "call":     InputPrivacyKeyPhoneCall,
    "invite":   InputPrivacyKeyChatInvite,
    "savedmusic": InputPrivacyKeySavedMusic,
}

PRIVACY_KEY_LABELS = {
    "photo":    "📸 Profile Photo",
    "phone":    "📱 Phone Number",
    "lastseen": "🕐 Last Seen",
    "bio":      "📝 Bio",
    "forward":  "↗️ Forwards",
    "call":     "📞 Calls",
    "invite":   "🔗 Group Invite",
    "savedmusic": "🎵 Saved Music",
}

PRIVACY_VALUE_MAP = {
    "everyone": InputPrivacyValueAllowAll,
    "contacts": InputPrivacyValueAllowContacts,
    "nobody":   InputPrivacyValueDisallowAll,
}

PRIVACY_VALUE_LABELS = {
    "everyone": "🌐 Everyone",
    "contacts": "👥 Contacts",
    "nobody":   "🔒 Nobody",
}


def _get_clone(automation, clone_idx: int):
    """Get clone client and name by 1-based index."""
    zero_idx = clone_idx - 1
    if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
        return None, None
    return automation.clone_clients[zero_idx], automation.clone_names[zero_idx]






async def _set_privacy(client, key_name: str, value_name: str, session_name: str) -> bool:
    """Set a single privacy key to a value."""
    key_class = PRIVACY_KEY_MAP.get(key_name)
    value_class = PRIVACY_VALUE_MAP.get(value_name)

    if key_class is None or value_class is None:
        logger.error(
            f"[CLONE-MGR] [{session_name}] Invalid privacy: "
            f"key={key_name}, value={value_name}"
        )
        return False

    try:
        await client(SetPrivacyRequest(
            key=key_class(),
            rules=[value_class()],
        ))
        logger.info(
            f"[CLONE-MGR] [{session_name}] ✓ {key_name} → {value_name}"
        )
        return True

    except FloodWaitError as e:
        logger.warning(
            f"[CLONE-MGR] [{session_name}] FloodWait {e.seconds}s "
            f"for {key_name}"
        )
        await asyncio.sleep(e.seconds + 1)
        try:
            await client(SetPrivacyRequest(
                key=key_class(),
                rules=[value_class()],
            ))
            return True
        except Exception:
            return False

    except Exception as e:
        logger.error(
            f"[CLONE-MGR] [{session_name}] Privacy failed "
            f"{key_name}→{value_name}: {type(e).__name__}: {e}"
        )
        return False






async def _set_all_privacy(client, value_name: str, session_name: str) -> dict:
    """Set ALL privacy keys to the same value."""
    results = {"success": 0, "failed": 0}

    for key_name in PRIVACY_KEY_MAP:
        ok = await _set_privacy(client, key_name, value_name, session_name)
        if ok:
            results["success"] += 1
        else:
            results["failed"] += 1

        
        await asyncio.sleep(0.3)

    return results






async def handle_privacy_action(event, data: str, automation):
    """
    Handle privacy action callbacks.

    Data formats:
        action:privacy:3:photo:everyone     → clone #3, photo → everyone
        action:privacy:3:photo:contacts     → clone #3, photo → contacts
        action:privacy:3:photo:nobody       → clone #3, photo → nobody
        action:privacy:3:all:nobody         → clone #3, ALL keys → nobody
        action:privacy:3:all:everyone       → clone #3, ALL keys → everyone
    """
    parts = data.split(":")
    

    if len(parts) < 5:
        await event.answer("⚠️ Invalid privacy action", alert=True)
        return

    try:
        clone_idx = int(parts[2])
    except ValueError:
        await event.answer("⚠️ Invalid clone index", alert=True)
        return

    key_name = parts[3]
    value_name = parts[4]

    client, name = _get_clone(automation, clone_idx)

    if client is None:
        await event.answer(f"⚠️ Clone #{clone_idx} not found", alert=True)
        return

    if not client.is_connected():
        await event.answer(f"⚠️ Clone #{clone_idx} is offline", alert=True)
        return

    
    if value_name not in PRIVACY_VALUE_MAP:
        await event.answer(f"⚠️ Invalid value: {value_name}", alert=True)
        return

    value_label = PRIVACY_VALUE_LABELS.get(value_name, value_name)

    
    if key_name == "all":
        await event.answer(f"⏳ Setting all privacy to {value_label}…")

        results = await _set_all_privacy(client, value_name, name)

        
        from ..menus.privacy_menu import build_privacy_clone_menu
        text, keyboard = build_privacy_clone_menu(automation, clone_idx)

        summary = (
            f"\n\n✅ All privacy → **{value_label}**\n"
            f"Success: {results['success']} | "
            f"Failed: {results['failed']}"
        )
        text += summary

        await event.edit(text, buttons=keyboard)
        return

    
    if key_name not in PRIVACY_KEY_MAP:
        await event.answer(f"⚠️ Unknown privacy key: {key_name}", alert=True)
        return

    key_label = PRIVACY_KEY_LABELS.get(key_name, key_name)

    ok = await _set_privacy(client, key_name, value_name, name)

    if ok:
        await event.answer(
            f"✅ #{clone_idx} {key_label} → {value_label}"
        )

        
        from ..menus.privacy_menu import build_privacy_clone_menu
        text, keyboard = build_privacy_clone_menu(automation, clone_idx)
        await event.edit(text, buttons=keyboard)
    else:
        await event.answer(
            f"❌ Failed to set {key_label} → {value_label}",
            alert=True,
        )






async def bulk_privacy_all_clones(automation, value_name: str) -> dict:
    """Set ALL privacy keys to value_name for ALL clones."""
    total_results = {"success": 0, "failed": 0, "skipped": 0}

    for i, (client, name) in enumerate(
        zip(automation.clone_clients, automation.clone_names)
    ):
        if not client.is_connected():
            total_results["skipped"] += 1
            continue

        results = await _set_all_privacy(client, value_name, name)
        total_results["success"] += results["success"]
        total_results["failed"] += results["failed"]

        
        await asyncio.sleep(0.5)

    return total_results


async def complete_bulk_privacy_target(
    event,
    target_text: str,
    mode: str,
    automation,
):
    """Show or hide every clone's profile photo for one user or chat."""
    target_text = target_text.strip()
    if not target_text:
        await event.reply("⚠️ Target cannot be empty")
        return

    results = {"success": 0, "failed": 0, "skipped": 0}
    for client, name in zip(automation.clone_clients, automation.clone_names):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        try:
            lookup = (
                int(target_text)
                if target_text.lstrip("-").isdigit()
                else target_text
            )
            entity = await client.get_entity(lookup)

            if isinstance(entity, User):
                if entity.access_hash is None:
                    raise ValueError("User has no access hash")
                target = InputUser(
                    user_id=entity.id,
                    access_hash=entity.access_hash,
                )
                if mode == "allow":
                    rules = [InputPrivacyValueAllowUsers(users=[target])]
                else:
                    rules = [
                        InputPrivacyValueDisallowUsers(users=[target]),
                        InputPrivacyValueAllowAll(),
                    ]
            elif isinstance(entity, Channel):
                target = InputPeerChannel(
                    channel_id=entity.id,
                    access_hash=entity.access_hash,
                )
                if mode == "allow":
                    rules = [
                        InputPrivacyValueAllowChatParticipants(
                            chats=[target],
                        )
                    ]
                else:
                    rules = [
                        InputPrivacyValueDisallowChatParticipants(
                            chats=[target],
                        ),
                        InputPrivacyValueAllowAll(),
                    ]
            elif isinstance(entity, Chat):
                target = InputPeerChat(chat_id=entity.id)
                if mode == "allow":
                    rules = [
                        InputPrivacyValueAllowChatParticipants(
                            chats=[target],
                        )
                    ]
                else:
                    rules = [
                        InputPrivacyValueDisallowChatParticipants(
                            chats=[target],
                        ),
                        InputPrivacyValueAllowAll(),
                    ]
            else:
                raise ValueError("Unsupported user, group, or channel")

            for privacy_key in (
                InputPrivacyKeyProfilePhoto(),
                InputPrivacyKeySavedMusic(),
            ):
                await client(SetPrivacyRequest(
                    key=privacy_key,
                    rules=rules,
                ))
            results["success"] += 1
        except FloodWaitError as exc:
            results["failed"] += 1
            await asyncio.sleep(exc.seconds + 1)
        except Exception:
            results["failed"] += 1
            logger.error(
                "[CLONE-MGR] [%s] Target profile-photo privacy failed",
                name,
                exc_info=True,
            )

    action = "shown to" if mode == "allow" else "hidden from"
    await event.reply(
        f"Profile photo {action} `{target_text}` for all clones.\n"
        f"Success: {results['success']} | "
        f"Skipped: {results['skipped']} | Failed: {results['failed']}"
    )

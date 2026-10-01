"""
Privacy engine — manages who can see profile photo, phone, last seen, etc.

Uses Telethon's GetPrivacyRequest/SetPrivacyRequest with a merge strategy:
  - Fetches existing privacy rules
  - Adds new users/chats to the allow-list (doesn't overwrite)
  - Base rule is always DISALLOW ALL (only whitelist can see)
"""

import logging
from typing import List, Optional, Set

from telethon import TelegramClient
from telethon.tl.functions.account import (
    GetPrivacyRequest,
    SetPrivacyRequest,
)
from telethon.tl.types import (
    
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyPhoneNumber,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyKeyChatInvite,
    InputPrivacyKeyPhoneCall,
    InputPrivacyKeyForwards,
    InputPrivacyKeyAbout,
    InputPrivacyKeySavedMusic,
    
    InputPrivacyValueDisallowAll,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowContacts,
    InputPrivacyValueAllowUsers,
    InputPrivacyValueAllowChatParticipants,
    InputPrivacyValueDisallowUsers,
    InputPrivacyValueDisallowChatParticipants,
    
    PrivacyValueAllowUsers,
    PrivacyValueAllowChatParticipants,
    PrivacyValueDisallowUsers,
    PrivacyValueDisallowChatParticipants,
    PrivacyValueAllowAll,
    PrivacyValueDisallowAll,
    PrivacyValueAllowContacts,
    
    InputPeerUser,
)

logger = logging.getLogger("TG-Auto")



PRIVACY_KEYS = {
    "photo":    InputPrivacyKeyProfilePhoto,
    "phone":    InputPrivacyKeyPhoneNumber,
    "lastseen": InputPrivacyKeyStatusTimestamp,
    "invite":   InputPrivacyKeyChatInvite,
    "call":     InputPrivacyKeyPhoneCall,
    "forward":  InputPrivacyKeyForwards,
    "bio":      InputPrivacyKeyAbout,
    "savedmusic": InputPrivacyKeySavedMusic,
    "saved_music": InputPrivacyKeySavedMusic,
    "music":     InputPrivacyKeySavedMusic,
}


def get_privacy_key(name: str):
    """Get privacy key class by name."""
    key_class = PRIVACY_KEYS.get(name.lower().strip())
    if key_class is None:
        raise ValueError(f"Unknown privacy key: {name}")
    return key_class()






async def get_current_allowed(client, key):
    """
    Get current allowed users + chats for a privacy key.
    Returns (allowed_user_ids: set, allowed_chat_ids: set, base_rule)
    """
    try:
        result = await client(GetPrivacyRequest(key=key))
    except Exception as e:
        logger.error(f"[PRIVACY] GetPrivacy failed: {e}")
        return set(), set(), None

    allowed_users = set()
    allowed_chats = set()
    base_rule = None

    for rule in result.rules:
        if isinstance(rule, PrivacyValueAllowUsers):
            allowed_users.update(rule.users)
        elif isinstance(rule, PrivacyValueAllowChatParticipants):
            allowed_chats.update(rule.chats)
        elif isinstance(rule, PrivacyValueDisallowAll):
            base_rule = "disallow_all"
        elif isinstance(rule, PrivacyValueAllowAll):
            base_rule = "allow_all"
        elif isinstance(rule, PrivacyValueAllowContacts):
            base_rule = "allow_contacts"

    return allowed_users, allowed_chats, base_rule






async def _build_input_peers(client, user_ids):
    """Convert user IDs to InputPeerUser objects."""
    peers = []
    for uid in user_ids:
        try:
            entity = await client.get_input_entity(uid)
            if isinstance(entity, InputPeerUser):
                peers.append(entity)
            else:
                logger.warning(f"[PRIVACY] Entity {uid} is not a user, skipping")
        except Exception as e:
            logger.debug(f"[PRIVACY] Can't get input for user {uid}: {e}")
    return peers






async def show_to_targets(
    client,
    key_name: str,
    user_ids=None,
    chat_ids=None,
    session_name: str = "Unknown",
) -> bool:
    """
    Add users/chats to the ALLOW list.
    Base rule is FORCED to disallow_all (only whitelist can see).
    Merges with existing whitelist.
    """
    try:
        key = get_privacy_key(key_name)
    except ValueError as e:
        logger.error(f"[PRIVACY] [{session_name}] {e}")
        return False

    user_ids = user_ids or []
    chat_ids = chat_ids or []

    if not user_ids and not chat_ids:
        logger.warning(f"[PRIVACY] [{session_name}] No targets to add")
        return False

    
    current_users, current_chats, _ = await get_current_allowed(client, key)

    
    merged_users = set(current_users) | set(user_ids)
    merged_chats = set(current_chats) | set(chat_ids)

    
    rules = []

    if merged_chats:
        rules.append(InputPrivacyValueAllowChatParticipants(
            chats=list(merged_chats)
        ))

    if merged_users:
        input_peers = await _build_input_peers(client, merged_users)
        if input_peers:
            rules.append(InputPrivacyValueAllowUsers(users=input_peers))

    rules.append(InputPrivacyValueDisallowAll())

    try:
        await client(SetPrivacyRequest(key=key, rules=rules))
        logger.info(
            f"[PRIVACY] [{session_name}] ✓ Show {key_name}: "
            f"+{len(user_ids)} user(s), +{len(chat_ids)} chat(s) "
            f"(total: {len(merged_users)}u, {len(merged_chats)}c)"
        )
        return True
    except Exception as e:
        logger.error(
            f"[PRIVACY] [{session_name}] SetPrivacy failed: "
            f"{type(e).__name__}: {e}"
        )
        return False


async def hide_from_targets(
    client,
    key_name: str,
    user_ids=None,
    chat_ids=None,
    session_name: str = "Unknown",
) -> bool:
    """
    Remove users/chats from the ALLOW list.
    Base rule stays disallow_all.
    """
    try:
        key = get_privacy_key(key_name)
    except ValueError as e:
        logger.error(f"[PRIVACY] [{session_name}] {e}")
        return False

    user_ids = user_ids or []
    chat_ids = chat_ids or []

    current_users, current_chats, _ = await get_current_allowed(client, key)

    merged_users = set(current_users) - set(user_ids)
    merged_chats = set(current_chats) - set(chat_ids)

    rules = []

    if merged_chats:
        rules.append(InputPrivacyValueAllowChatParticipants(
            chats=list(merged_chats)
        ))

    if merged_users:
        input_peers = await _build_input_peers(client, merged_users)
        if input_peers:
            rules.append(InputPrivacyValueAllowUsers(users=input_peers))

    rules.append(InputPrivacyValueDisallowAll())

    try:
        await client(SetPrivacyRequest(key=key, rules=rules))
        logger.info(
            f"[PRIVACY] [{session_name}] ✓ Hide {key_name}: "
            f"-{len(user_ids)} user(s), -{len(chat_ids)} chat(s) "
            f"(remaining: {len(merged_users)}u, {len(merged_chats)}c)"
        )
        return True
    except Exception as e:
        logger.error(
            f"[PRIVACY] [{session_name}] SetPrivacy failed: "
            f"{type(e).__name__}: {e}"
        )
        return False






async def force_hide_all(
    client,
    key_name: str = "photo",
    keep_whitelist: bool = True,
    session_name: str = "Unknown",
) -> bool:
    """Force base rule to DISALLOW ALL, optionally keep whitelist."""
    try:
        key = get_privacy_key(key_name)
    except ValueError as e:
        logger.error(f"[PRIVACY] [{session_name}] {e}")
        return False
    if keep_whitelist:
        current_users, current_chats, _ = await get_current_allowed(client, key)
    else:
        current_users, current_chats = set(), set()

    rules = []

    if current_chats:
        rules.append(InputPrivacyValueAllowChatParticipants(
            chats=list(current_chats)
        ))

    if current_users:
        input_peers = await _build_input_peers(client, current_users)
        if input_peers:
            rules.append(InputPrivacyValueAllowUsers(users=input_peers))

    rules.append(InputPrivacyValueDisallowAll())

    try:
        await client(SetPrivacyRequest(key=key, rules=rules))
        logger.info(
            f"[PRIVACY] [{session_name}] ✓ {key_name} → FORCED to Nobody "
            f"(kept whitelist: {len(current_users)}u, {len(current_chats)}c)"
        )
        return True
    except Exception as e:
        logger.error(f"[PRIVACY] [{session_name}] Force hide failed: {e}")
        return False


async def set_exact_privacy(
    client,
    key_name: str,
    allowed_user_ids=None,
    allowed_chat_ids=None,
    session_name: str = "Unknown",
) -> bool:
    """Replace a privacy key with an exact disallow-all whitelist."""
    try:
        key = get_privacy_key(key_name)
        allowed_user_ids = set(allowed_user_ids or [])
        allowed_chat_ids = set(allowed_chat_ids or [])
        rules = []

        if allowed_chat_ids:
            rules.append(
                InputPrivacyValueAllowChatParticipants(
                    chats=list(allowed_chat_ids)
                )
            )
        if allowed_user_ids:
            peers = await _build_input_peers(client, allowed_user_ids)
            if peers:
                rules.append(InputPrivacyValueAllowUsers(users=peers))

        rules.append(InputPrivacyValueDisallowAll())

        await client(SetPrivacyRequest(key=key, rules=rules))
        logger.info(
            "[PRIVACY] [%s] %s exact policy applied: %s user(s), %s chat(s)",
            session_name,
            key_name,
            len(allowed_user_ids),
            len(allowed_chat_ids),
        )
        return True
    except Exception as exc:
        logger.error(
            "[PRIVACY] [%s] %s exact policy failed: %s",
            session_name,
            key_name,
            exc,
        )
        return False


async def apply_clone_default_privacy(
    client,
    main_user_id: int,
    session_name: str = "Clone",
    source_photo_policy=None,
) -> dict:
    """Apply the default privacy policy for one clone.

    New clones start with a private profile photo. When a source policy is
    available, its readable profile-photo exceptions are copied to the new
    clone without changing the other privacy defaults.
    """
    from privacy_targets import allowed_ids
    from privacy_apply_state import (
        clone_policy_applied,
        mark_clone_policy,
    )

    stored_users, stored_chats = allowed_ids()
    default_users = sorted(set([main_user_id, *stored_users]))
    default_chats = sorted(set(stored_chats))
    allowed_users = default_users
    allowed_chats = default_chats
    if source_photo_policy is not None:
        photo_users = set(source_photo_policy[0])
        photo_users.add(main_user_id)
        allowed_users = sorted(photo_users)
        allowed_chats = sorted(set(source_photo_policy[1]))
    results = {}
    for key_name, allowed in (
        ("photo", allowed_users),
        ("savedmusic", default_users),
        ("lastseen", default_users),
        ("call", []),
        ("forward", []),
        ("invite", []),
    ):
        if clone_policy_applied(session_name, key_name):
            results[key_name] = True
            logger.info(
                "[PRIVACY] [%s] %s already applied; skipping",
                session_name,
                key_name,
            )
            continue
        results[key_name] = await set_exact_privacy(
            client=client,
            key_name=key_name,
            allowed_user_ids=allowed,
            allowed_chat_ids=allowed_chats,
            session_name=session_name,
        )
        if results[key_name]:
            mark_clone_policy(session_name, key_name)
    return results


async def apply_clone_defaults(automation) -> dict:
    """Apply defaults and clone Clone1's profile-photo visibility policy."""
    main_user_id = getattr(automation, "_admin_user_id", None)
    if main_user_id is None:
        return {"success": 0, "failed": len(automation.clone_clients)}

    totals = {"success": 0, "failed": 0}
    source_photo_policy = None
    for source_client, source_name in zip(
        automation.clone_clients,
        automation.clone_names,
    ):
        if source_name.lower() != "clone1":
            continue
        try:
            source_photo_policy = await get_current_allowed(
                source_client,
                get_privacy_key("photo"),
            )
            logger.info(
                "[PRIVACY] Clone1 photo policy loaded: %s user(s), %s chat(s)",
                len(source_photo_policy[0]),
                len(source_photo_policy[1]),
            )
        except Exception as exc:
            logger.warning(
                "[PRIVACY] Could not read Clone1 photo policy: %s",
                exc,
            )
        break

    for client, name in zip(
        automation.clone_clients,
        automation.clone_names,
    ):
        try:
            if not client.is_connected():
                totals["failed"] += 1
                continue
            results = await apply_clone_default_privacy(
                client,
                main_user_id,
                name,
                source_photo_policy=source_photo_policy,
            )
            if all(results.values()):
                totals["success"] += 1
            else:
                totals["failed"] += 1
        except Exception as exc:
            logger.error(
                "[PRIVACY] [%s] Default policy failed: %s",
                name,
                exc,
            )
            totals["failed"] += 1
    return totals


async def apply_saved_main_photo_targets(automation) -> dict:
    """Apply newly saved profile-photo targets to Main once."""
    from privacy_targets import allowed_ids
    from privacy_apply_state import (
        mark_main_photo_targets,
        unmarked_main_photo_targets,
    )

    user_ids, chat_ids = allowed_ids()
    user_ids, chat_ids = unmarked_main_photo_targets(user_ids, chat_ids)
    if not user_ids and not chat_ids:
        return {"applied": 0, "failed": 0}

    ok = await show_to_targets(
        client=automation.main_client,
        key_name="photo",
        user_ids=user_ids,
        chat_ids=chat_ids,
        session_name="Main",
    )
    if ok:
        mark_main_photo_targets(user_ids, chat_ids)
        return {"applied": len(user_ids) + len(chat_ids), "failed": 0}
    return {"applied": 0, "failed": len(user_ids) + len(chat_ids)}






async def get_privacy_status_readable(
    client,
    key_name: str = "photo",
    session_name: str = "Unknown",
) -> dict:
    """
    Get human-readable privacy status.
    Returns dict with rules info.
    """
    try:
        key = get_privacy_key(key_name)
    except ValueError as e:
        return {"error": str(e)}

    try:
        result = await client(GetPrivacyRequest(key=key))
    except Exception as e:
        return {"error": f"GetPrivacy failed: {e}"}

    info = {
        "base_rule": "unknown",
        "allowed_users": [],
        "allowed_chats": [],
        "disallowed_users": [],
        "disallowed_chats": [],
    }

    for rule in result.rules:
        cls_name = rule.__class__.__name__

        if cls_name == "PrivacyValueAllowAll":
            info["base_rule"] = "everyone"
        elif cls_name == "PrivacyValueDisallowAll":
            info["base_rule"] = "nobody"
        elif cls_name == "PrivacyValueAllowContacts":
            info["base_rule"] = "contacts"
        elif cls_name == "PrivacyValueAllowUsers":
            info["allowed_users"] = list(rule.users)
        elif cls_name == "PrivacyValueAllowChatParticipants":
            info["allowed_chats"] = list(rule.chats)
        elif cls_name == "PrivacyValueDisallowUsers":
            info["disallowed_users"] = list(rule.users)
        elif cls_name == "PrivacyValueDisallowChatParticipants":
            info["disallowed_chats"] = list(rule.chats)

    return info

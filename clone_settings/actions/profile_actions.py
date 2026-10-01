"""
Profile actions — change name, bio, username, photo for individual clones.
"""

import os
import asyncio
import logging

from telethon.tl.functions.account import (
    UpdateProfileRequest,
    UpdateUsernameRequest,
)
from telethon.tl.functions.photos import (
    DeletePhotosRequest,
    GetUserPhotosRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.types import InputPhoto
from telethon.errors import (
    FloodWaitError,
    UsernameOccupiedError,
    UsernameInvalidError,
    UsernameNotModifiedError,
)

from ..utils.keyboards import make_inline_keyboard

logger = logging.getLogger("CloneManager")


def _get_clone(automation, clone_idx: int):
    """Get clone client and name by 1-based index."""
    zero_idx = clone_idx - 1
    if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
        return None, None
    return automation.clone_clients[zero_idx], automation.clone_names[zero_idx]


async def handle_profile_action(event, data: str, automation, set_pending_input):
    """
    Route profile actions.

    Data formats:
        action:profile:3:first_name
        action:profile:3:last_name
        action:profile:3:bio
        action:profile:3:username
        action:profile:3:remove_username
        action:profile:3:photo
        action:profile:3:remove_photo
    """
    parts = data.split(":")
    # parts: ["action", "profile", clone_idx, action_type]

    if len(parts) < 4:
        await event.answer("⚠️ Invalid action format", alert=True)
        return

    try:
        clone_idx = int(parts[2])
    except ValueError:
        await event.answer("⚠️ Invalid clone index", alert=True)
        return

    action_type = parts[3]
    client, name = _get_clone(automation, clone_idx)

    if client is None:
        await event.answer(f"⚠️ Clone #{clone_idx} not found", alert=True)
        return

    if not client.is_connected():
        await event.answer(f"⚠️ Clone #{clone_idx} is offline", alert=True)
        return

    # ─── Actions that need text input ───────────────────────────
    if action_type == "first_name":
        set_pending_input(event.sender_id, "profile:name", {
            "clone_idx": clone_idx,
            "field": "first_name",
        })
        await event.edit(
            f"✏️ **Change First Name — Clone #{clone_idx}**\n\n"
            f"Current: {_get_current_name(client, 'first')}\n\n"
            f"Send the new first name:\n"
            f"(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "last_name":
        set_pending_input(event.sender_id, "profile:name", {
            "clone_idx": clone_idx,
            "field": "last_name",
        })
        await event.edit(
            f"✏️ **Change Last Name — Clone #{clone_idx}**\n\n"
            f"Current: {_get_current_name(client, 'last')}\n\n"
            f"Send the new last name:\n"
            f"(send `.` to clear, or `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type in ("name",):
        # Legacy "name" action → redirect to first_name
        set_pending_input(event.sender_id, "profile:name", {
            "clone_idx": clone_idx,
            "field": "first_name",
        })
        await event.edit(
            f"✏️ **Change Name — Clone #{clone_idx}**\n\n"
            f"Send the new first name:\n"
            f"(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "bio":
        set_pending_input(event.sender_id, "profile:bio", {
            "clone_idx": clone_idx,
        })
        await event.edit(
            f"📝 **Change Bio — Clone #{clone_idx}**\n\n"
            f"Send the new bio (max 70 chars):\n"
            f"(send `.` to clear, or `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "username":
        set_pending_input(event.sender_id, "profile:username", {
            "clone_idx": clone_idx,
        })
        await event.edit(
            f"🔗 **Change Username — Clone #{clone_idx}**\n\n"
            f"Current: {_get_current_username(client)}\n\n"
            f"Send the new username (without @):\n"
            f"(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "photo":
        set_pending_input(event.sender_id, "profile:photo", {
            "clone_idx": clone_idx,
        })
        await event.edit(
            f"📸 **Set Profile Photo — Clone #{clone_idx}**\n\n"
            "Reply to this message with a photo.\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    # ─── Actions that execute immediately ───────────────────────
    if action_type == "remove_username":
        await _remove_username(event, client, name, clone_idx)
        return

    if action_type == "remove_photo":
        await _remove_photo(event, client, name, clone_idx)
        return

    await event.answer(f"❓ Unknown profile action: {action_type}", alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: get current values
# ─────────────────────────────────────────────────────────────────────────────

def _get_current_name(client, which: str) -> str:
    try:
        if hasattr(client, "_self_user") and client._self_user:
            if which == "first":
                return client._self_user.first_name or "—"
            elif which == "last":
                return client._self_user.last_name or "—"
    except Exception:
        pass
    return "—"


def _get_current_username(client) -> str:
    try:
        if hasattr(client, "_self_user") and client._self_user:
            u = client._self_user.username
            return f"@{u}" if u else "—"
    except Exception:
        pass
    return "—"


# ─────────────────────────────────────────────────────────────────────────────
# Complete text input actions
# ─────────────────────────────────────────────────────────────────────────────

async def complete_name_change(event, text: str, input_data: dict, automation):
    """Complete a name change after receiving text input."""
    clone_idx = input_data.get("clone_idx")
    field = input_data.get("field", "first_name")

    client, name = _get_clone(automation, clone_idx)
    if client is None:
        await event.reply(f"⚠️ Clone #{clone_idx} not found")
        return

    # Handle clear
    if text == ".":
        if field == "first_name":
            await event.reply("⚠️ First name cannot be empty")
            return
        text = ""

    try:
        apply_now = True
        if input_data.get("normal_mode"):
            # Normal Mode changes are staged in cloneN.json and applied only
            # through the Normal Mode Apply button.
            apply_now = False
            text = text[:64]

        if apply_now:
            if field == "first_name":
                await client(UpdateProfileRequest(first_name=text))
            elif field == "last_name":
                await client(UpdateProfileRequest(last_name=text))

        display_text = text if text else "(cleared)"
        logger.info(
            f"[CLONE-MGR] [{name}] ✓ {field} changed to: {display_text}"
        )

        if apply_now:
            # Refresh cached user info
            try:
                client._self_user = await client.get_me()
            except Exception:
                pass

        if input_data.get("normal_mode"):
            from ..normal_mode_store import save_clone_field
            save_clone_field(clone_idx, field, text)

        status = "changed" if apply_now else "saved for Normal Mode"
        await event.reply(
            f"✅ Clone #{clone_idx} {field.replace('_', ' ')} "
            f"{status}: **{display_text}**"
        )

    except FloodWaitError as e:
        await event.reply(
            f"⚠️ FloodWait: please wait {e.seconds}s and try again"
        )
    except Exception as e:
        logger.error(f"[CLONE-MGR] [{name}] Name change failed: {e}")
        await event.reply(f"❌ Failed: {type(e).__name__}: {e}")


async def complete_bio_change(event, text: str, input_data: dict, automation):
    """Complete a bio change after receiving text input."""
    clone_idx = input_data.get("clone_idx")

    client, name = _get_clone(automation, clone_idx)
    if client is None:
        await event.reply(f"⚠️ Clone #{clone_idx} not found")
        return

    # Handle clear
    if text == ".":
        text = ""

    # Truncate to 70 chars (Telegram limit)
    if len(text) > 70:
        text = text[:70]

    try:
        apply_now = True
        if input_data.get("normal_mode"):
            # Keep Normal Mode edits staged until Apply is pressed.
            apply_now = False

        if apply_now:
            await client(UpdateProfileRequest(about=text))

        if input_data.get("normal_mode"):
            from ..normal_mode_store import save_clone_field
            save_clone_field(clone_idx, "bio", text)

        display_text = text if text else "(cleared)"
        logger.info(f"[CLONE-MGR] [{name}] ✓ Bio changed to: {display_text}")

        status = "changed" if apply_now else "saved for Normal Mode"
        await event.reply(
            f"✅ Clone #{clone_idx} bio {status}:\n"
            f"**{display_text}**"
        )

    except FloodWaitError as e:
        await event.reply(
            f"⚠️ FloodWait: please wait {e.seconds}s and try again"
        )
    except Exception as e:
        logger.error(f"[CLONE-MGR] [{name}] Bio change failed: {e}")
        await event.reply(f"❌ Failed: {type(e).__name__}: {e}")


async def complete_username_change(event, text: str, input_data: dict, automation):
    """Complete a username change after receiving text input."""
    clone_idx = input_data.get("clone_idx")

    client, name = _get_clone(automation, clone_idx)
    if client is None:
        await event.reply(f"⚠️ Clone #{clone_idx} not found")
        return

    # Clean username
    username = text.strip().lstrip("@")

    try:
        await client(UpdateUsernameRequest(username=username))

        logger.info(
            f"[CLONE-MGR] [{name}] ✓ Username changed to: @{username}"
        )

        try:
            client._self_user = await client.get_me()
        except Exception:
            pass

        await event.reply(
            f"✅ Clone #{clone_idx} username changed to: @{username}"
        )

    except UsernameOccupiedError:
        await event.reply(f"❌ Username @{username} is already taken")
    except UsernameInvalidError:
        await event.reply(
            f"❌ Username @{username} is invalid.\n"
            f"Rules: 5-32 chars, a-z, 0-9, underscore"
        )
    except UsernameNotModifiedError:
        await event.reply(f"ℹ️ Username is already @{username}")
    except FloodWaitError as e:
        await event.reply(
            f"⚠️ FloodWait: please wait {e.seconds}s"
        )
    except Exception as e:
        logger.error(f"[CLONE-MGR] [{name}] Username change failed: {e}")
        await event.reply(f"❌ Failed: {type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Immediate actions
# ─────────────────────────────────────────────────────────────────────────────

async def _remove_username(event, client, name: str, clone_idx: int):
    """Remove username from a clone."""
    try:
        await client(UpdateUsernameRequest(username=""))
        logger.info(f"[CLONE-MGR] [{name}] ✓ Username removed")

        try:
            client._self_user = await client.get_me()
        except Exception:
            pass

        from ..menus.profile_menu import build_profile_clone_menu
        from ..bot_core import get_automation

        text, keyboard = build_profile_clone_menu(get_automation(), clone_idx)
        await event.edit(text, buttons=keyboard)
        await event.answer("✅ Username removed")

    except FloodWaitError as e:
        await event.answer(f"⚠️ FloodWait: {e.seconds}s", alert=True)
    except Exception as e:
        logger.error(f"[CLONE-MGR] [{name}] Remove username failed: {e}")
        await event.answer(f"❌ Failed: {e}", alert=True)


async def _remove_photo(event, client, name: str, clone_idx: int):
    """Remove all profile photos from a clone."""
    try:
        me = await client.get_me()
        photos_result = await client(GetUserPhotosRequest(
            user_id=me,
            offset=0,
            max_id=0,
            limit=100,
        ))

        if not photos_result.photos:
            await event.answer("ℹ️ No profile photos to remove", alert=True)
            return

        input_photos = [
            InputPhoto(
                id=p.id,
                access_hash=p.access_hash,
                file_reference=p.file_reference,
            )
            for p in photos_result.photos
        ]

        await client(DeletePhotosRequest(id=input_photos))
        count = len(input_photos)

        logger.info(
            f"[CLONE-MGR] [{name}] ✓ Removed {count} profile photo(s)"
        )

        from ..menus.profile_menu import build_profile_clone_menu
        from ..bot_core import get_automation

        text, keyboard = build_profile_clone_menu(get_automation(), clone_idx)
        await event.edit(text, buttons=keyboard)
        await event.answer(f"✅ Removed {count} photo(s)")

    except FloodWaitError as e:
        await event.answer(f"⚠️ FloodWait: {e.seconds}s", alert=True)
    except Exception as e:
        logger.error(f"[CLONE-MGR] [{name}] Remove photo failed: {e}")
        await event.answer(f"❌ Failed: {e}", alert=True)

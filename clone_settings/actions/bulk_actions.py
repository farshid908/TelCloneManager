"""
Bulk actions — apply profile/privacy/name/bio changes to ALL clones at once.
"""

import asyncio
import logging
import re

from telethon.tl.functions.account import UpdateProfileRequest
from telethon.errors import FloodWaitError

from ..utils.keyboards import make_inline_keyboard

logger = logging.getLogger("CloneManager")


def _get_clone_number(session_name: str, fallback: int) -> int:
    """Use the numeric suffix in the session name when one exists."""
    match = re.search(r"(\d+)$", session_name or "")
    return int(match.group(1)) if match else fallback


async def handle_bulk_action(event, data: str, automation, set_pending_input):
    """
    Handle bulk action callbacks.

    Data formats:
        action:bulk:photo              → set same photo for all
        action:bulk:photo_wm           → set photo with watermark
        action:bulk:remove_photo       → remove all photos
        action:bulk:name               → set same name for all
        action:bulk:name_template      → set template name (User_{N})
        action:bulk:bio                → set same bio for all
        action:bulk:clear_bio          → clear all bios
        action:bulk:privacy:hideall    → all privacy → nobody
        action:bulk:privacy:showall    → all privacy → everyone
        action:bulk:privacy:contacts   → all privacy → contacts
    """
    parts = data.split(":")

    if len(parts) < 3:
        await event.answer("⚠️ Invalid bulk action", alert=True)
        return

    action_type = parts[2]

    if action_type == "privacy_target" and len(parts) >= 4:
        mode = "allow" if parts[3] == "allow" else "deny"
        set_pending_input(
            event.sender_id,
            "bulk:privacy_target",
            {"mode": mode},
        )
        await event.edit(
            "Send a User ID, Chat ID, username, group/channel link, "
            "or `cancel`."
        )
        await event.answer()
        return

    # Sub-action for privacy
    if action_type == "privacy" and len(parts) >= 4:
        sub = parts[3]
        await _bulk_privacy(event, sub, automation)
        return

    # ─── Photo actions ──────────────────────────────────────────
    if action_type == "photo":
        set_pending_input(event.sender_id, "bulk:photo", {"watermark": False})
        await event.edit(
            "📸 **Bulk Set Photo — All Clones**\n\n"
            "Send a photo to set for ALL clones.\n"
            "Each clone will get the same photo.\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "photo_wm":
        set_pending_input(event.sender_id, "bulk:photo", {"watermark": True})
        await event.edit(
            "📸 **Bulk Photo + Watermark — All Clones**\n\n"
            "Send a photo. Each clone will get it with\n"
            "their name watermarked on it.\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "remove_photo":
        await _bulk_remove_photo(event, automation)
        return

    # ─── Name actions ───────────────────────────────────────────
    if action_type == "name":
        set_pending_input(event.sender_id, "bulk:name", {})
        await event.edit(
            "✏️ **Bulk Set Name — All Clones**\n\n"
            "Send the name to set for ALL clones.\n"
            "Every clone will get the same first name.\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "name_template":
        set_pending_input(event.sender_id, "bulk:name_template", {})
        await event.edit(
            "✏️ **Bulk Template Name — All Clones**\n\n"
            "Send a name template using `{N}` for clone number.\n\n"
            "Examples:\n"
            "  `User_{N}` → User_1, User_2, User_3…\n"
            "  `Player {N}` → Player 1, Player 2…\n"
            "  `⭐ Star{N}` → ⭐ Star1, ⭐ Star2…\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "name_number":
        set_pending_input(event.sender_id, "bulk:name_number_base", {})
        await event.edit(
            "Clone Names + Numbers\n\n"
            "Send the base name, for example: `Clone`.\n"
            "You will then choose whether the number goes before or after it."
        )
        await event.answer()
        return

    # ─── Bio actions ────────────────────────────────────────────
    if action_type == "bio":
        set_pending_input(event.sender_id, "bulk:bio", {})
        await event.edit(
            "📝 **Bulk Set Bio — All Clones**\n\n"
            "Send the bio text for ALL clones (max 70 chars).\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "clear_bio":
        await _bulk_clear_bio(event, automation)
        return

    await event.answer(f"❓ Unknown bulk action: {action_type}", alert=True)


# ─────────────────────────────────────────────────────────────────────────────
# Complete text input handlers
# ─────────────────────────────────────────────────────────────────────────────

async def complete_bulk_name(event, text: str, automation):
    """Set the same first name for all clones."""
    if not text:
        await event.reply("⚠️ Name cannot be empty")
        return

    total = len(automation.clone_clients)
    status_msg = await event.reply(
        f"✏️ Setting name **\"{text}\"** for {total} clone(s)…"
    )

    results = {"success": 0, "failed": 0, "skipped": 0}
    from ..normal_mode_store import (
        clear_clone_mode_overrides,
        save_clone_mode_field,
    )
    save_clone_mode_field("first_name", text)
    clear_clone_mode_overrides("first_name")

    for client, name in zip(automation.clone_clients, automation.clone_names):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        try:
            await client(UpdateProfileRequest(first_name=text))
            results["success"] += 1
            logger.info(f"[CLONE-MGR] [{name}] ✓ Name → {text}")

            try:
                client._self_user = await client.get_me()
            except Exception:
                pass

        except FloodWaitError as e:
            results["failed"] += 1
            logger.warning(f"[CLONE-MGR] [{name}] FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Name change failed: {e}")

        await asyncio.sleep(0.5)

    try:
        await status_msg.edit(
            f"✅ **Bulk Name Complete**\n\n"
            f"Name: **{text}**\n"
            f"✓ Success: {results['success']}\n"
            f"⏭ Skipped: {results['skipped']}\n"
            f"✗ Failed: {results['failed']}"
        )
    except Exception:
        pass


async def complete_bulk_name_template(event, template: str, automation):
    """Set template-based names for all clones. {N} = clone number."""
    if "{N}" not in template and "{n}" not in template:
        await event.reply(
            "⚠️ Template must contain `{N}` placeholder.\n"
            "Example: `User_{N}`"
        )
        return

    total = len(automation.clone_clients)
    status_msg = await event.reply(
        f"✏️ Applying template **\"{template}\"** to {total} clone(s)…"
    )

    results = {"success": 0, "failed": 0, "skipped": 0}
    from ..normal_mode_store import save_clone_mode_field
    save_clone_mode_field("first_name", "")

    for i, (client, name) in enumerate(
        zip(automation.clone_clients, automation.clone_names)
    ):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        clone_num = _get_clone_number(name, i + 1)
        new_name = template.replace("{N}", str(clone_num))
        new_name = new_name.replace("{n}", str(clone_num))
        from ..normal_mode_store import save_clone_mode_clone_field
        save_clone_mode_clone_field(clone_num, "first_name", new_name)

        try:
            await client(UpdateProfileRequest(first_name=new_name))
            results["success"] += 1
            logger.info(f"[CLONE-MGR] [{name}] ✓ Name → {new_name}")

            try:
                client._self_user = await client.get_me()
            except Exception:
                pass

        except FloodWaitError as e:
            results["failed"] += 1
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Template name failed: {e}")

        await asyncio.sleep(0.5)

    try:
        await status_msg.edit(
            f"✅ **Bulk Template Name Complete**\n\n"
            f"Template: **{template}**\n"
            f"✓ Success: {results['success']}\n"
            f"⏭ Skipped: {results['skipped']}\n"
            f"✗ Failed: {results['failed']}"
        )
    except Exception:
        pass


async def complete_bulk_bio(event, text: str, automation):
    """Set the same bio for all clones."""
    # Truncate to 70 chars
    if len(text) > 70:
        text = text[:70]

    total = len(automation.clone_clients)
    status_msg = await event.reply(
        f"📝 Setting bio for {total} clone(s)…"
    )

    results = {"success": 0, "failed": 0, "skipped": 0}
    from ..normal_mode_store import save_clone_mode_field
    save_clone_mode_field("bio", text)

    for client, name in zip(automation.clone_clients, automation.clone_names):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        try:
            await client(UpdateProfileRequest(about=text))
            results["success"] += 1
            logger.info(f"[CLONE-MGR] [{name}] ✓ Bio updated")

        except FloodWaitError as e:
            results["failed"] += 1
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Bio change failed: {e}")

        await asyncio.sleep(0.5)

    try:
        await status_msg.edit(
            f"✅ **Bulk Bio Complete**\n\n"
            f"Bio: **{text}**\n"
            f"✓ Success: {results['success']}\n"
            f"⏭ Skipped: {results['skipped']}\n"
            f"✗ Failed: {results['failed']}"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Immediate bulk actions
# ─────────────────────────────────────────────────────────────────────────────

async def _bulk_remove_photo(event, automation):
    """Remove profile photo from all clones."""
    from telethon.tl.functions.photos import (
        DeletePhotosRequest,
        GetUserPhotosRequest,
    )
    from telethon.tl.types import InputPhoto

    total = len(automation.clone_clients)
    await event.answer(f"🗑️ Removing photos from {total} clone(s)…")

    results = {"success": 0, "failed": 0, "skipped": 0, "no_photo": 0}

    for client, name in zip(automation.clone_clients, automation.clone_names):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        try:
            me = await client.get_me()
            photos = await client(GetUserPhotosRequest(
                user_id=me, offset=0, max_id=0, limit=100,
            ))

            if not photos.photos:
                results["no_photo"] += 1
                continue

            input_photos = [
                InputPhoto(
                    id=p.id,
                    access_hash=p.access_hash,
                    file_reference=p.file_reference,
                )
                for p in photos.photos
            ]

            await client(DeletePhotosRequest(id=input_photos))
            results["success"] += 1
            logger.info(
                f"[CLONE-MGR] [{name}] ✓ Removed {len(input_photos)} photo(s)"
            )

        except FloodWaitError as e:
            results["failed"] += 1
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Remove photo failed: {e}")

        await asyncio.sleep(0.5)

    from ..menus.bulk_actions import build_bulk_menu
    text, keyboard = build_bulk_menu(automation)

    text += (
        f"\n\n🗑️ **Bulk Remove Photo Complete**\n"
        f"✓ Removed: {results['success']}\n"
        f"📭 No photo: {results['no_photo']}\n"
        f"⏭ Skipped: {results['skipped']}\n"
        f"✗ Failed: {results['failed']}"
    )

    await event.edit(text, buttons=keyboard)


async def complete_bulk_name_number(
    event,
    base_name: str,
    position: str,
    automation,
):
    """Set Clone1/1Clone-style first names for all clones."""
    position = position.strip().lower()
    if position not in {"1", "2", "after", "before", "suffix", "prefix"}:
        await event.reply(
            "Choose 1 for after the name or 2 for before the name."
        )
        return

    suffix = position in {"1", "after", "suffix"}
    results = {"success": 0, "failed": 0, "skipped": 0}
    from ..normal_mode_store import save_clone_mode_field
    save_clone_mode_field("first_name", "")

    for fallback_index, (client, name) in enumerate(
        zip(automation.clone_clients, automation.clone_names),
        start=1,
    ):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        clone_number = _get_clone_number(name, fallback_index)
        new_name = (
            f"{base_name}{clone_number}"
            if suffix
            else f"{clone_number}{base_name}"
        )
        from ..normal_mode_store import save_clone_mode_clone_field
        save_clone_mode_clone_field(
            clone_number,
            "first_name",
            new_name,
        )
        try:
            await client(UpdateProfileRequest(first_name=new_name))
            results["success"] += 1
            try:
                client._self_user = await client.get_me()
            except Exception:
                pass
        except FloodWaitError as exc:
            results["failed"] += 1
            await asyncio.sleep(exc.seconds + 1)
        except Exception:
            results["failed"] += 1
            logger.error(
                "[CLONE-MGR] [%s] Numbered name change failed",
                name,
                exc_info=True,
            )
        await asyncio.sleep(0.5)

    await event.reply(
        f"Numbered names complete: {results['success']} succeeded, "
        f"{results['skipped']} skipped, {results['failed']} failed."
    )


async def _bulk_clear_bio(event, automation):
    """Clear bio for all clones."""
    total = len(automation.clone_clients)
    await event.answer(f"🗑️ Clearing bios for {total} clone(s)…")

    results = {"success": 0, "failed": 0, "skipped": 0}

    for client, name in zip(automation.clone_clients, automation.clone_names):
        if not client.is_connected():
            results["skipped"] += 1
            continue

        try:
            await client(UpdateProfileRequest(about=""))
            results["success"] += 1
            logger.info(f"[CLONE-MGR] [{name}] ✓ Bio cleared")
        except FloodWaitError as e:
            results["failed"] += 1
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Clear bio failed: {e}")

        await asyncio.sleep(0.5)

    from ..menus.bulk_actions import build_bulk_menu
    text, keyboard = build_bulk_menu(automation)

    text += (
        f"\n\n🗑️ **Bulk Clear Bio Complete**\n"
        f"✓ Cleared: {results['success']}\n"
        f"⏭ Skipped: {results['skipped']}\n"
        f"✗ Failed: {results['failed']}"
    )

    await event.edit(text, buttons=keyboard)


async def _bulk_privacy(event, value_name: str, automation):
    """Set all privacy keys for all clones."""
    from .privacy_actions import bulk_privacy_all_clones, PRIVACY_VALUE_LABELS

    if value_name == "hideall":
        privacy_value = "nobody"
    elif value_name == "showall":
        privacy_value = "everyone"
    elif value_name == "contacts":
        privacy_value = "contacts"
    else:
        await event.answer(f"⚠️ Unknown privacy preset: {value_name}", alert=True)
        return

    value_label = PRIVACY_VALUE_LABELS.get(privacy_value, privacy_value)
    total = len(automation.clone_clients)

    await event.answer(
        f"🔒 Setting all privacy → {value_label} for {total} clone(s)…"
    )

    results = await bulk_privacy_all_clones(automation, privacy_value)

    from ..menus.bulk_actions import build_bulk_menu
    text, keyboard = build_bulk_menu(automation)

    text += (
        f"\n\n🔒 **Bulk Privacy Complete**\n"
        f"Target: **{value_label}**\n"
        f"✓ Success: {results['success']}\n"
        f"⏭ Skipped: {results['skipped']}\n"
        f"✗ Failed: {results['failed']}"
    )

    await event.edit(text, buttons=keyboard)

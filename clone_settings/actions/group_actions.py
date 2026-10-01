"""
Group actions — join/leave groups for all clones.
"""

import asyncio
import logging
import re

from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.types import Channel
from telethon.errors import (
    FloodWaitError,
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    ChannelPrivateError,
)

from ..utils.keyboards import make_inline_keyboard

logger = logging.getLogger("CloneManager")

INVITE_RE = re.compile(
    r'(?:https?://)?(?:t\.me/\+|t\.me/joinchat/)([a-zA-Z0-9_-]+)'
)
USERNAME_RE = re.compile(
    r'(?:(?:https?://)?t\.me/|@)([a-zA-Z][a-zA-Z0-9_]{3,})'
)


async def handle_group_action(event, data: str, automation, set_pending_input):
    """
    Handle group join/leave actions.

    Data formats:
        action:group:join      → ask for link, then join all clones
        action:group:leave     → ask for link/username, then leave all clones
    """
    parts = data.split(":")

    if len(parts) < 3:
        await event.answer("⚠️ Invalid action", alert=True)
        return

    action_type = parts[2]

    if action_type == "join":
        set_pending_input(event.sender_id, "group:join", {})
        await event.edit(
            "🚀 **Join Group — All Clones**\n\n"
            "Send the group link or @username:\n\n"
            "Accepted formats:\n"
            "  • `https://t.me/+ABC123`\n"
            "  • `https://t.me/joinchat/ABC123`\n"
            "  • `https://t.me/groupname`\n"
            "  • `@groupname`\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    if action_type == "leave":
        set_pending_input(event.sender_id, "group:leave", {})
        await event.edit(
            "👋 **Leave Group — All Clones**\n\n"
            "Send the group link, @username, or group ID:\n\n"
            "(or send `cancel` to abort)"
        )
        await event.answer()
        return

    await event.answer(f"❓ Unknown group action: {action_type}", alert=True)


async def complete_join_group(event, text: str, automation):
    """Complete join group after receiving link/username."""

    
    invite_hash = None
    username = None

    invite_match = INVITE_RE.search(text)
    if invite_match:
        invite_hash = invite_match.group(1)
    else:
        username_match = USERNAME_RE.search(text)
        if username_match:
            username = username_match.group(1)
        else:
            
            clean = text.strip().lstrip("@")
            if clean and len(clean) >= 4:
                username = clean

    if not invite_hash and not username:
        await event.reply(
            "⚠️ Could not parse link or username.\n"
            "Please send a valid Telegram group link."
        )
        return

    label = invite_hash or f"@{username}"
    status_msg = await event.reply(
        f"🚀 Joining **{label}** with "
        f"{len(automation.clone_clients)} clone(s)…"
    )

    results = {"success": 0, "failed": 0, "already": 0, "flood": 0}

    for client, name in zip(automation.clone_clients, automation.clone_names):
        try:
            if invite_hash:
                try:
                    await client(ImportChatInviteRequest(hash=invite_hash))
                    results["success"] += 1
                    logger.info(f"[CLONE-MGR] [{name}] ✓ Joined via invite")
                except UserAlreadyParticipantError:
                    results["already"] += 1
                except (InviteHashExpiredError, InviteHashInvalidError) as e:
                    results["failed"] += 1
                    logger.error(f"[CLONE-MGR] [{name}] Invalid invite: {e}")
            else:
                try:
                    entity = await client.get_entity(username)
                    await client(JoinChannelRequest(entity))
                    results["success"] += 1
                    logger.info(f"[CLONE-MGR] [{name}] ✓ Joined @{username}")
                except UserAlreadyParticipantError:
                    results["already"] += 1
                except ChannelPrivateError:
                    results["failed"] += 1

        except FloodWaitError as e:
            results["flood"] += 1
            logger.warning(f"[CLONE-MGR] [{name}] FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Join failed: {e}")

        await asyncio.sleep(2)

    try:
        await status_msg.edit(
            f"✅ **Join Complete: {label}**\n\n"
            f"✓ Joined: {results['success']}\n"
            f"⏭ Already in: {results['already']}\n"
            f"⏳ FloodWait: {results['flood']}\n"
            f"✗ Failed: {results['failed']}"
        )
    except Exception:
        pass


async def complete_leave_group(event, text: str, automation):
    """Complete leave group after receiving link/username/ID."""

    
    target = text.strip()

    
    try:
        chat_id = int(target)
    except ValueError:
        chat_id = None

    
    username = None
    if chat_id is None:
        username_match = USERNAME_RE.search(target)
        if username_match:
            username = username_match.group(1)
        else:
            clean = target.lstrip("@")
            if clean and len(clean) >= 4:
                username = clean

    if chat_id is None and username is None:
        await event.reply("⚠️ Please send a valid group ID or @username")
        return

    label = f"@{username}" if username else str(chat_id)
    status_msg = await event.reply(
        f"👋 Leaving **{label}** with "
        f"{len(automation.clone_clients)} clone(s)…"
    )

    results = {"success": 0, "failed": 0, "not_in": 0}

    for client, name in zip(automation.clone_clients, automation.clone_names):
        try:
            
            entity = None
            try:
                if username:
                    entity = await client.get_entity(username)
                elif chat_id:
                    entity = await client.get_entity(chat_id)
            except Exception:
                results["not_in"] += 1
                continue

            if entity is None:
                results["not_in"] += 1
                continue

            if isinstance(entity, Channel):
                await client(LeaveChannelRequest(entity))
                results["success"] += 1
                logger.info(f"[CLONE-MGR] [{name}] ✓ Left {label}")
            else:
                results["failed"] += 1

        except FloodWaitError as e:
            results["failed"] += 1
            await asyncio.sleep(e.seconds + 1)
        except Exception as e:
            results["failed"] += 1
            logger.error(f"[CLONE-MGR] [{name}] Leave failed: {e}")

        await asyncio.sleep(1)

    try:
        await status_msg.edit(
            f"✅ **Leave Complete: {label}**\n\n"
            f"✓ Left: {results['success']}\n"
            f"⏭ Not in: {results['not_in']}\n"
            f"✗ Failed: {results['failed']}"
        )
    except Exception:
        pass

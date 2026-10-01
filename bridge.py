"""
Bridge module — helps clones resolve peers they haven't interacted with.

Strategy:
  1. Get @username from Main → pass to clones (fastest)
  2. If no username, forward a message from source → bridge group
     Wait for clones to sync
     Have all clones fetch dialogs to update their cache
"""

import asyncio
import logging
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.messages import ForwardMessagesRequest

logger = logging.getLogger("TG-Auto")


async def get_username_from_main(
    main_client: TelegramClient,
    entity_id: int,
) -> Optional[str]:
    """Try to get @username of an entity via Main."""
    try:
        entity = await main_client.get_entity(entity_id)
        username = getattr(entity, "username", None)
        if username:
            return f"@{username}"
    except Exception as e:
        logger.debug(f"[BRIDGE] Can't get username for {entity_id}: {e}")
    return None


async def _fetch_dialogs_on_clones(
    clone_clients: list,
    clone_names: list,
    limit: int = 30,
):
    """Force all clones to refresh their entity cache via get_dialogs."""
    async def refresh(client, name):
        try:
            async for _ in client.iter_dialogs(limit=limit):
                pass
            logger.debug(f"[BRIDGE] [{name}] Dialogs refreshed")
        except Exception as e:
            logger.debug(f"[BRIDGE] [{name}] Dialog refresh failed: {e}")

    tasks = [refresh(c, n) for c, n in zip(clone_clients, clone_names)]
    await asyncio.gather(*tasks, return_exceptions=True)


async def forward_via_bridge(
    main_client: TelegramClient,
    entity_id: int,
    bridge_group_id: int,
    clone_clients: list,
    clone_names: list,
) -> bool:
    """
    Forward a message from source (entity_id) to bridge group.
    This teaches all clone sessions the peer's access_hash via update.
    """
    if not clone_clients:
        return False

    # ─── Get source entity ──────────────────────────────────────
    try:
        source_entity = await main_client.get_entity(entity_id)
    except Exception as e:
        logger.error(f"[BRIDGE] Can't get source entity {entity_id}: {e}")
        return False

    # ─── Get bridge entity ──────────────────────────────────────
    try:
        bridge_entity = await main_client.get_entity(bridge_group_id)
    except Exception as e:
        logger.error(f"[BRIDGE] Can't get bridge group {bridge_group_id}: {e}")
        return False

    # ─── Find a message to forward ──────────────────────────────
    marker_msg = None
    sent_by_us = False

    # Try to get a recent message from source
    try:
        history = await main_client.get_messages(source_entity, limit=1)
        if history and len(history) > 0 and history[0] is not None:
            marker_msg = history[0]
            logger.info(
                f"[BRIDGE] Using existing message from history "
                f"(msg_id={marker_msg.id})"
            )
    except Exception as e:
        logger.debug(f"[BRIDGE] Can't fetch history: {e}")

    # If no history, send a marker
    if marker_msg is None:
        try:
            # Non-empty, non-emoji-only message
            marker_msg = await main_client.send_message(
                source_entity, "bridge_marker_ping"
            )
            sent_by_us = True
            logger.info(
                f"[BRIDGE] Sent marker to {entity_id} (msg_id={marker_msg.id})"
            )
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"[BRIDGE] Can't send marker: {e}")
            return False

    if marker_msg is None:
        logger.error("[BRIDGE] No message available to forward")
        return False

    # ─── Forward marker to bridge group ─────────────────────────
    try:
        await main_client(ForwardMessagesRequest(
            from_peer=source_entity,
            id=[marker_msg.id],
            to_peer=bridge_entity,
            with_my_score=False,
            drop_author=False,
        ))
        logger.info(f"[BRIDGE] Forwarded marker to bridge group")
    except FloodWaitError as e:
        logger.warning(f"[BRIDGE] FloodWait {e.seconds}s during forward")
        # Clean up sent marker
        if sent_by_us:
            try:
                await main_client.delete_messages(source_entity, [marker_msg.id])
            except Exception:
                pass
        return False
    except Exception as e:
        logger.error(f"[BRIDGE] Forward failed: {e}")
        if sent_by_us:
            try:
                await main_client.delete_messages(source_entity, [marker_msg.id])
            except Exception:
                pass
        return False

    # ─── Wait a bit for update propagation ──────────────────────
    logger.info("[BRIDGE] Waiting 3s for update propagation…")
    await asyncio.sleep(3)

    # ─── Force clones to refresh their dialogs (updates cache) ──
    logger.info(
        f"[BRIDGE] Refreshing dialogs on {len(clone_clients)} clone(s)…"
    )
    await _fetch_dialogs_on_clones(clone_clients, clone_names, limit=30)

    # ─── Delete the marker we sent ──────────────────────────────
    if sent_by_us:
        try:
            await main_client.delete_messages(source_entity, [marker_msg.id])
            logger.debug("[BRIDGE] Deleted marker message")
        except Exception as e:
            logger.debug(f"[BRIDGE] Can't delete marker: {e}")

    # ─── Verify clones can resolve now ──────────────────────────
    success = 0
    for client, name in zip(clone_clients, clone_names):
        try:
            await client.get_entity(entity_id)
            success += 1
        except Exception as e:
            logger.debug(f"[BRIDGE] [{name}] Still can't resolve: {e}")

    logger.info(
        f"[BRIDGE] {success}/{len(clone_clients)} clones can now resolve {entity_id}"
    )

    return success > 0


async def prepare_entity_for_clones(
    main_client: TelegramClient,
    entity_id,
    clone_clients: list,
    clone_names: list,
    bridge_group_id: Optional[int] = None,
):
    """
    Make sure clones can resolve the target entity.

    Returns dict:
        {
            "success": bool,
            "method": "username" | "bridge" | "already_ok" | "failed",
            "target": str | int  (@username or original id)
        }
    """
    if not clone_clients:
        return {"success": False, "method": "no_clones", "target": entity_id}

    # ─── Check 1: are clones already OK? ────────────────────────
    already_ok = 0
    for client in clone_clients:
        try:
            await client.get_entity(entity_id)
            already_ok += 1
        except Exception:
            pass

    if already_ok == len(clone_clients):
        return {
            "success": True,
            "method": "already_ok",
            "target": entity_id,
        }

    # ─── Check 2: does target have @username? ──────────────────
    username = await get_username_from_main(main_client, entity_id)
    if username:
        logger.info(f"[BRIDGE] Entity has public username: {username}")
        return {
            "success": True,
            "method": "username",
            "target": username,
        }

    # ─── Check 3: forward via bridge group ─────────────────────
    if bridge_group_id is not None:
        logger.info(
            f"[BRIDGE] No username — trying bridge forward for {entity_id}"
        )
        bridge_ok = await forward_via_bridge(
            main_client=main_client,
            entity_id=entity_id,
            bridge_group_id=bridge_group_id,
            clone_clients=clone_clients,
            clone_names=clone_names,
        )
        if bridge_ok:
            return {
                "success": True,
                "method": "bridge",
                "target": entity_id,
            }

    return {
        "success": False,
        "method": "failed",
        "target": entity_id,
    }

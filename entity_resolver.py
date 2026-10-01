"""
Entity resolution module.
Resolves Telegram entities by @username (global) or integer ID (cache).
Includes bridge fallback for private users.
"""

import asyncio
import logging
from typing import Optional, Union
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.tl.types import User, Chat, Channel
from telethon.errors import (
    FloodWaitError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
)

logger = logging.getLogger("TG-Auto")


@dataclass
class ChatTarget:
    """Resolution result for a target chat."""
    chat_id: int
    username: Optional[str] = None
    entity_type: str = "unknown"
    display_name: str = ""
    bridge_seeded: bool = False
    send_target: Union[str, int] = 0

    def __post_init__(self) -> None:
        if self.send_target == 0:
            self.send_target = self.username if self.username else self.chat_id

    @property
    def has_username(self) -> bool:
        return self.username is not None

    def __str__(self) -> str:
        return (
            f"ChatTarget(id={self.chat_id}, username={self.username!r}, "
            f"type={self.entity_type}, send_via={self.send_target!r})"
        )


def get_display_name(entity) -> str:
    if isinstance(entity, User):
        parts = [entity.first_name or "", entity.last_name or ""]
        return " ".join(p for p in parts if p).strip() or str(entity.id)
    if isinstance(entity, (Chat, Channel)):
        return getattr(entity, "title", str(entity.id))
    return str(getattr(entity, "id", "unknown"))


def classify_entity(entity) -> str:
    if isinstance(entity, User):
        return "user"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


async def resolve_entity(
    client: TelegramClient,
    target: Union[str, int],
    session_name: str = "Unknown",
    silent: bool = False,
) -> Optional[object]:
    """
    Resolve entity by @username (global) or integer ID (cache).
    silent: if True, don't log errors (used for bridge probe).
    """
    try:
        return await client.get_entity(target)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds + 1)
        try:
            return await client.get_entity(target)
        except Exception:
            return None
    except (UsernameNotOccupiedError, UsernameInvalidError) as e:
        if not silent:
            logger.error(f"[{session_name}] Invalid username {target!r}: {e}")
        return None
    except Exception as e:
        if not silent:
            logger.error(
                f"[{session_name}] Resolve {target!r}: {type(e).__name__}: {e}"
            )
        return None


async def resolve_entity_with_bridge(
    client: TelegramClient,
    target,
    session_name: str = "Unknown",
    main_client: Optional[TelegramClient] = None,
    bridge_group_id: Optional[int] = None,
    other_clones: Optional[list] = None,
    other_names: Optional[list] = None,
):
    """
    Resolve entity with bridge fallback.

    Steps:
      1. Try direct resolution (silent)
      2. If fails and bridge context available → try bridge
      3. Retry with new target after bridge
    """
    # First attempt — silent so we don't spam errors
    entity = await resolve_entity(client, target, session_name, silent=True)
    if entity is not None:
        return entity

    # Bridge fallback
    if main_client is not None and bridge_group_id is not None:
        try:
            from bridge import prepare_entity_for_clones

            logger.info(
                f"[{session_name}] Attempting bridge fallback for {target}…"
            )

            result = await prepare_entity_for_clones(
                main_client=main_client,
                entity_id=target,
                clone_clients=other_clones or [client],
                clone_names=other_names or [session_name],
                bridge_group_id=bridge_group_id,
            )

            if result["success"]:
                new_target = result["target"]
                logger.info(
                    f"[{session_name}] Bridge {result['method']} — "
                    f"retrying with {new_target}"
                )
                # Retry, this time NOT silent so we see errors
                entity = await resolve_entity(
                    client, new_target, session_name, silent=False,
                )
                if entity is not None:
                    return entity
                logger.error(
                    f"[{session_name}] Post-bridge resolve still failed"
                )
            else:
                logger.error(
                    f"[{session_name}] Bridge {result['method']} for {target}"
                )

        except Exception as e:
            logger.error(f"[{session_name}] Bridge fallback error: {e}")

    # Final failure — log now
    logger.error(
        f"[{session_name}] Failed to resolve {target} even after bridge"
    )
    return None


async def auto_extract_chat_target(
    main_client: TelegramClient,
    event,
    clone_clients,
    clone_names,
    bridge_group,
) -> ChatTarget:
    """Auto-extract best send-target from event chat."""
    from bridge import prepare_entity_for_clones

    chat_id = event.chat_id

    try:
        entity = await main_client.get_entity(chat_id)
    except Exception as e:
        logger.error(f"[RESOLVE] Main.get_entity({chat_id}) failed: {e}")
        return ChatTarget(
            chat_id=chat_id, entity_type="unknown",
            display_name=str(chat_id), send_target=chat_id,
        )

    raw_username = getattr(entity, "username", None)
    display_name = get_display_name(entity)
    entity_type = classify_entity(entity)

    if raw_username:
        formatted = f"@{raw_username}"
        logger.info(
            f"[RESOLVE] ✓ Username: {formatted} ({entity_type}: {display_name})"
        )
        return ChatTarget(
            chat_id=chat_id, username=formatted,
            entity_type=entity_type, display_name=display_name,
            send_target=formatted,
        )

    logger.warning(f"[RESOLVE] No username for '{display_name}' (ID={chat_id})")

    seeded = False
    if bridge_group is not None and clone_clients:
        try:
            result = await prepare_entity_for_clones(
                main_client=main_client,
                entity_id=chat_id,
                clone_clients=clone_clients,
                clone_names=clone_names,
                bridge_group_id=bridge_group,
            )
            seeded = result.get("success", False)
            if seeded and result.get("method") == "username":
                # Bridge found a username — use it
                new_target = result["target"]
                logger.info(f"[RESOLVE] Using discovered username: {new_target}")
                return ChatTarget(
                    chat_id=chat_id, username=new_target,
                    entity_type=entity_type, display_name=display_name,
                    bridge_seeded=True, send_target=new_target,
                )
        except Exception as e:
            logger.error(f"[RESOLVE] Bridge seeding failed: {e}")

    return ChatTarget(
        chat_id=chat_id, username=None, entity_type=entity_type,
        display_name=display_name, bridge_seeded=seeded, send_target=chat_id,
    )


__TCM_FILE_HASH__ = "loop_manager00002"

import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger("TG-Auto")

LoopKey = Union[Tuple[int, str], Tuple[int, str, str]]


def make_loop_key(
    chat_id: int, text: str, scope: Optional[str] = None
) -> LoopKey:
    """Create a loop key, optionally scoped to an independent send job."""
    if scope is None:
        return (chat_id, text)
    return (chat_id, text, scope)


async def cancel_loop_task(task: asyncio.Task, key: LoopKey) -> None:
    """Cancel a loop task and wait for cleanup."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"[LOOP] Cancel wait error for chat={key[0]} text=\"{key[1]}\": {e}")

    logger.info(f'[LOOP] Task cancelled: chat={key[0]} text="{key[1]}"')


class LoopRegistry:
    """Manages all active loop tasks keyed by (chat_id, text)."""

    def __init__(self):
        self.active: Dict[LoopKey, asyncio.Task] = {}

    async def register(self, key: LoopKey, task: asyncio.Task):
        """Register a new loop, cancelling any existing one with the same key."""
        existing = self.active.get(key)
        if existing is not None:
            logger.info(
                f'[LOOP] Replacing existing: chat={key[0]} text="{key[1]}"'
            )
            await cancel_loop_task(existing, key)

        self.active[key] = task
        logger.info(
            f'[LOOP] ✓ Registered: chat={key[0]} text="{key[1]}" '
            f"(total: {len(self.active)})"
        )

    async def cancel_by_key(self, key: LoopKey) -> bool:
        """Cancel a loop, or all scoped jobs matching a base key."""
        if len(key) == 2:
            matching_keys = [
                active_key for active_key in self.active
                if active_key[:2] == key
            ]
        else:
            matching_keys = [key] if key in self.active else []
        if not matching_keys:
            return False
        for matching_key in matching_keys:
            task = self.active.pop(matching_key, None)
            if task is not None:
                await cancel_loop_task(task, matching_key)
        return True

    async def cancel_all(self) -> int:
        """Cancel all loops. Returns count."""
        items = list(self.active.items())
        self.active.clear()

        for key, task in items:
            await cancel_loop_task(task, key)

        return len(items)

    def in_chat(self, chat_id: int) -> List[LoopKey]:
        """Return all loop keys active in a chat."""
        return [k for k in self.active if k[0] == chat_id]

    def is_active(self, key: LoopKey) -> bool:
        """Return whether a live task is already registered for ``key``."""
        task = self.active.get(key)
        if task is None:
            return False
        if task.done():
            self.active.pop(key, None)
            return False
        return True

    def remove(self, key: LoopKey):
        """Remove a key without cancelling (used in finally blocks)."""
        self.active.pop(key, None)

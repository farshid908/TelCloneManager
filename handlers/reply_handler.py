"""
Reply sender — sends a message as a reply to another message.
"""

__TCM_FILE_HASH__ = "9734242528"


import asyncio
import logging
from telethon.errors import FloodWaitError

logger = logging.getLogger("TG-Auto")


async def send_reply(
    client,
    chat_id: int,
    target_message,
    reply_text: str,
    session_name: str = "Unknown",
) -> bool:
    """
    Send a text as a reply to a specific message.
    Returns True on success.
    """
    try:
        await client.send_message(
            chat_id,
            reply_text,
            reply_to=target_message.id,
        )
        logger.info(
            f"[REPLY] [{session_name}] ✓ Replied to msg_id={target_message.id} "
            f"with \"{reply_text[:40]}\""
        )
        return True
    except FloodWaitError as e:
        logger.warning(
            f"[REPLY] [{session_name}] FloodWait {e.seconds}s"
        )
        await asyncio.sleep(e.seconds + 1)
        try:
            await client.send_message(
                chat_id,
                reply_text,
                reply_to=target_message.id,
            )
            logger.info(
                f"[REPLY] [{session_name}] ✓ Replied (after FloodWait)"
            )
            return True
        except Exception as e2:
            logger.error(f"[REPLY] [{session_name}] ✗ Retry failed: {e2}")
            return False
    except Exception as e:
        logger.error(
            f"[REPLY] [{session_name}] ✗ Failed: {type(e).__name__}: {e}"
        )
        return False

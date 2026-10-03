"""
Handler for 'set prof' command.

Downloads the replied photo/video, watermarks it with the clone's name
(unless -nowater), then sets it as each clone's profile picture/video.

Main is NEVER modified — only clones.
"""

__TCM_FILE_HASH__ = "2397038324"


import os
import asyncio
import tempfile
import shutil
import logging
from telethon import events

from command_parser import parse_set_prof_command
from watermark_engine import watermark_file
from profile_manager import set_profile_media

logger = logging.getLogger("TG-Auto")

SETPROF_AUTO_DELETE_DELAY = 3.0


async def _auto_delete_after(client, chat_id, msg_ids, delay):
    """Wait then delete messages."""
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_ids)
    except Exception:
        pass


def _clone_name_to_label(clone_name: str) -> str:
    """
    Convert clone filename to display label.
    'Clone3' → 'CLONE3'
    'Clone3.session' → 'CLONE3'
    """
    name = clone_name
    if name.endswith(".session"):
        name = name[:-len(".session")]
    return name.upper()


def _is_supported_media(message) -> bool:
    """Check if replied message is a photo or short video."""
    if message.photo:
        return True
    if message.video:
        
        try:
            for attr in message.video.attributes:
                if hasattr(attr, "duration"):
                    return attr.duration <= 11
        except Exception:
            pass
        return True  
    return False


def register_setprof_handler(automation):
    """Register the set prof command handler."""
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r"""^set\s+prof""", from_users=aid,
    ))
    async def _setprof(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_set_prof_command(event.raw_text)
            if not parsed:
                return

            no_watermark = parsed["no_watermark"]

            
            if not event.is_reply:
                warn = await event.reply(
                    "⚠️ Reply to a photo or short video (≤10s) with:\n"
                    "`set prof` — with watermark (default)\n"
                    "`set prof -nowater` — no watermark"
                )
                asyncio.create_task(
                    _auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id],
                        SETPROF_AUTO_DELETE_DELAY,
                    )
                )
                return

            reply_msg = await event.get_reply_message()

            if not _is_supported_media(reply_msg):
                warn = await event.reply(
                    "⚠️ Replied message must be a photo or short video (≤10s)"
                )
                asyncio.create_task(
                    _auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id],
                        SETPROF_AUTO_DELETE_DELAY,
                    )
                )
                return

            if not automation.clone_clients:
                warn = await event.reply("⚠️ No clones available")
                asyncio.create_task(
                    _auto_delete_after(
                        automation.main_client, event.chat_id,
                        [event.id, warn.id],
                        SETPROF_AUTO_DELETE_DELAY,
                    )
                )
                return

            
            temp_dir = tempfile.mkdtemp(prefix="tg_setprof_")
            try:
                logger.info(
                    f"[SETPROF] Downloading media to {temp_dir} "
                    f"(watermark={not no_watermark})"
                )

                downloaded = await automation.main_client.download_media(
                    reply_msg,
                    file=temp_dir,
                )

                if not downloaded or not os.path.isfile(downloaded):
                    logger.error(f"[SETPROF] Download failed")
                    return

                logger.info(f"[SETPROF] Downloaded: {downloaded}")

                
                results = {"success": 0, "failed": 0}

                for client, name in zip(
                    automation.clone_clients,
                    automation.clone_names,
                ):
                    try:
                        label = _clone_name_to_label(name)

                        if no_watermark:
                            
                            file_to_upload = downloaded
                        else:
                            
                            wm_output = watermark_file(
                                downloaded,
                                text=label,
                                session_name=name,
                                settings=(
                                    __import__(
                                        "clone_settings.normal_mode_store",
                                        fromlist=["load_watermark_settings"],
                                    ).load_watermark_settings()
                                ),
                            )
                            if wm_output is None:
                                logger.error(
                                    f"[SETPROF] [{name}] Watermark failed, "
                                    f"skipping"
                                )
                                results["failed"] += 1
                                continue
                            file_to_upload = wm_output

                        
                        ok = await set_profile_media(
                            client=client,
                            file_path=file_to_upload,
                            session_name=name,
                        )

                        if ok:
                            results["success"] += 1
                        else:
                            results["failed"] += 1

                        
                        if not no_watermark and file_to_upload != downloaded:
                            try:
                                os.remove(file_to_upload)
                            except Exception:
                                pass

                        
                        await asyncio.sleep(1)

                    except Exception as e:
                        logger.error(
                            f"[SETPROF] [{name}] Error: "
                            f"{type(e).__name__}: {e}"
                        )
                        results["failed"] += 1

                
                mode_desc = "no watermark" if no_watermark else "with watermark"
                logger.info(
                    f"[SETPROF] ✓ Done: {results['success']} success, "
                    f"{results['failed']} failed ({mode_desc})"
                )

            finally:
                
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

            
            asyncio.create_task(
                _auto_delete_after(
                    automation.main_client, event.chat_id,
                    [event.id],
                    SETPROF_AUTO_DELETE_DELAY,
                )
            )

        except Exception as e:
            logger.error(
                f"[SETPROF] Handler error: {type(e).__name__}: {e}",
                exc_info=True,
            )

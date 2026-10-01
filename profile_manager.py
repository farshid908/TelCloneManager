"""
Profile manager — set profile photo/video for accounts.

Ensures only ONE profile picture exists at a time:
  1. Delete every existing profile photo
  2. Upload the new photo/video with proper metadata
"""

import os
import asyncio
import logging
import subprocess
from typing import Optional

from telethon import TelegramClient
from telethon.tl.functions.photos import (
    UploadProfilePhotoRequest,
    DeletePhotosRequest,
    GetUserPhotosRequest,
)
from telethon.tl.types import InputPhoto
from telethon.errors import FloodWaitError

logger = logging.getLogger("TG-Auto")


# ─────────────────────────────────────────────────────────────────────────────
# Video info helper
# ─────────────────────────────────────────────────────────────────────────────

def _get_video_info(file_path: str) -> Optional[dict]:
    """
    Use ffprobe to get video duration, dimensions, etc.
    Returns dict or None if failed.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration,codec_name,pix_fmt",
                "-of", "default=noprint_wrappers=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None

        info = {}
        for line in result.stdout.strip().split("\n"):
            if "=" in line:
                key, val = line.split("=", 1)
                info[key.strip()] = val.strip()

        if "duration" in info:
            try:
                info["duration"] = float(info["duration"])
            except ValueError:
                info["duration"] = 0.0

        for k in ("width", "height"):
            if k in info:
                try:
                    info[k] = int(info[k])
                except ValueError:
                    info[k] = 0

        return info
    except Exception as e:
        logger.debug(f"[PROFILE] ffprobe error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Delete all existing profile photos
# ─────────────────────────────────────────────────────────────────────────────

async def _delete_all_profile_photos(
    client: TelegramClient,
    session_name: str = "Unknown",
) -> int:
    """Delete every profile photo of the current account."""
    total_deleted = 0

    try:
        me = await client.get_me()
    except Exception as e:
        logger.error(f"[PROFILE] [{session_name}] get_me failed: {e}")
        return 0

    try:
        offset = 0
        page_size = 100
        input_photos = []

        while True:
            result = await client(GetUserPhotosRequest(
                user_id=me,
                offset=offset,
                max_id=0,
                limit=page_size,
            ))

            if not result.photos:
                break

            for photo in result.photos:
                input_photos.append(InputPhoto(
                    id=photo.id,
                    access_hash=photo.access_hash,
                    file_reference=photo.file_reference,
                ))

            if len(result.photos) < page_size:
                break
            offset += page_size

        if not input_photos:
            logger.info(
                f"[PROFILE] [{session_name}] No existing profile photos"
            )
            return 0

        await client(DeletePhotosRequest(id=input_photos))
        total_deleted = len(input_photos)
        logger.info(
            f"[PROFILE] [{session_name}] 🗑️  Deleted {total_deleted} "
            f"existing profile photo(s)"
        )

    except FloodWaitError as e:
        logger.warning(
            f"[PROFILE] [{session_name}] FloodWait {e.seconds}s during delete"
        )
        await asyncio.sleep(e.seconds + 1)
    except Exception as e:
        logger.error(
            f"[PROFILE] [{session_name}] Delete failed: "
            f"{type(e).__name__}: {e}"
        )

    return total_deleted


# ─────────────────────────────────────────────────────────────────────────────
# Set profile media
# ─────────────────────────────────────────────────────────────────────────────

async def set_profile_media(
    client: TelegramClient,
    file_path: str,
    session_name: str = "Unknown",
) -> bool:
    """
    Set profile photo or video for the account.

    Steps:
      1. Delete all existing profile photos
      2. Validate video (if applicable) via ffprobe
      3. Upload the new one
    """
    if not os.path.isfile(file_path):
        logger.error(f"[PROFILE] [{session_name}] File not found: {file_path}")
        return False

    ext = os.path.splitext(file_path)[1].lower()
    is_video = ext in {".mp4", ".mov", ".webm"}

    # ─── Validate video before uploading ────────────────────────
    if is_video:
        info = _get_video_info(file_path)
        if info:
            logger.info(
                f"[PROFILE] [{session_name}] Video info: "
                f"{info.get('width')}x{info.get('height')}, "
                f"{info.get('duration', 0):.1f}s, "
                f"codec={info.get('codec_name')}, "
                f"pix_fmt={info.get('pix_fmt')}"
            )

            # Check Telegram's requirements
            duration = info.get("duration", 0)
            width = info.get("width", 0)
            height = info.get("height", 0)
            codec = info.get("codec_name", "")
            pix_fmt = info.get("pix_fmt", "")

            problems = []
            if duration > 10.5:
                problems.append(f"duration {duration:.1f}s > 10s")
            if duration < 0.1:
                problems.append("duration too short")
            if width != height:
                problems.append(f"not square ({width}x{height})")
            if width > 800 or height > 800:
                problems.append(f"too large ({width}x{height})")
            if codec != "h264":
                problems.append(f"codec is {codec}, need h264")
            if pix_fmt != "yuv420p":
                problems.append(f"pix_fmt is {pix_fmt}, need yuv420p")

            if problems:
                logger.error(
                    f"[PROFILE] [{session_name}] Video invalid: "
                    f"{', '.join(problems)}"
                )
                return False
        else:
            logger.warning(
                f"[PROFILE] [{session_name}] Can't verify video with ffprobe, "
                f"trying anyway"
            )

    # ─── Step 1: Delete all existing profile photos ─────────────
    await _delete_all_profile_photos(client, session_name=session_name)
    await asyncio.sleep(1)

    # ─── Step 2: Upload the new one ─────────────────────────────
    try:
        if is_video:
            # For video profile, use send_file first to get proper InputFile
            # then extract it for UploadProfilePhotoRequest
            uploaded_video = await client.upload_file(
                file_path,
                part_size_kb=512,
            )

            await client(UploadProfilePhotoRequest(
                file=None,
                video=uploaded_video,
                video_start_ts=0.0,
            ))
            logger.info(
                f"[PROFILE] [{session_name}] ✓ Video profile set from "
                f"{os.path.basename(file_path)}"
            )
        else:
            uploaded = await client.upload_file(file_path)
            await client(UploadProfilePhotoRequest(
                file=uploaded,
            ))
            logger.info(
                f"[PROFILE] [{session_name}] ✓ Photo profile set from "
                f"{os.path.basename(file_path)}"
            )

        return True

    except FloodWaitError as e:
        logger.warning(
            f"[PROFILE] [{session_name}] FloodWait {e.seconds}s during upload"
        )
        await asyncio.sleep(e.seconds + 1)
        try:
            if is_video:
                uploaded_video = await client.upload_file(
                    file_path, part_size_kb=512,
                )
                await client(UploadProfilePhotoRequest(
                    file=None,
                    video=uploaded_video,
                    video_start_ts=0.0,
                ))
            else:
                uploaded = await client.upload_file(file_path)
                await client(UploadProfilePhotoRequest(file=uploaded))
            logger.info(f"[PROFILE] [{session_name}] ✓ Retry succeeded")
            return True
        except Exception as e2:
            logger.error(f"[PROFILE] [{session_name}] Retry failed: {e2}")
            return False

    except Exception as e:
        error_msg = str(e)
        logger.error(
            f"[PROFILE] [{session_name}] Upload failed: "
            f"{type(e).__name__}: {error_msg}"
        )

        if is_video and "video" in error_msg.lower():
            logger.info(
                f"[PROFILE] [{session_name}] Hint: Video requirements:\n"
                f"  • Duration: ≤ 10s\n"
                f"  • Square (equal width & height)\n"
                f"  • Max 800x800\n"
                f"  • Codec: H.264 (yuv420p)\n"
                f"  • No audio track"
            )

        return False

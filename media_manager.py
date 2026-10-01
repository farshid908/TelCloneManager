"""
Media manager — save and send voice messages, video messages, music, and videos.

Media types:
  vim  = Video Message (circle/round video)
  vom  = Voice Message (audio note)
  mus  = Music file (audio)
  vid  = Video file

All files stored in ./media/ directory with a JSON registry.
"""

import os
import json
import asyncio
import subprocess
import logging
from typing import Dict, List, Optional

from telethon import TelegramClient

logger = logging.getLogger("TG-Auto")

MEDIA_DIR = "./media"
REGISTRY_FILE = os.path.join(MEDIA_DIR, "_registry.json")

# Valid media types
MEDIA_TYPES = {
    "vim": {
        "label": "Video Message",
        "extensions": [".mp4"],
        "emoji": "⭕",
    },
    "vom": {
        "label": "Voice Message",
        "extensions": [".ogg", ".oga"],
        "emoji": "🎤",
    },
    "mus": {
        "label": "Music",
        "extensions": [".mp3", ".m4a", ".ogg", ".flac"],
        "emoji": "🎵",
    },
    "vid": {
        "label": "Video",
        "extensions": [".mp4", ".mov", ".mkv", ".webm"],
        "emoji": "🎬",
    },
}


def _ensure_dirs():
    """Create media directory if needed."""
    os.makedirs(MEDIA_DIR, exist_ok=True)


def _load_registry() -> Dict:
    """Load media registry from disk."""
    _ensure_dirs()
    if not os.path.exists(REGISTRY_FILE):
        return {}
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[MEDIA] Registry load failed: {e}")
        return {}


def _save_registry(registry: Dict):
    """Save media registry to disk."""
    _ensure_dirs()
    try:
        with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[MEDIA] Registry save failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Save media
# ─────────────────────────────────────────────────────────────────────────────

async def save_media_from_message(
    client: TelegramClient,
    message,
    media_type: str,
    name: str,
    session_name: str = "Main",
) -> Dict:
    """
    Download media from a replied message and save it.

    media_type: "vim", "vom", "mus", "vid"
    name: user-defined name (e.g., "5", "hello", "num_1")

    Returns dict with success/message.
    """
    _ensure_dirs()

    if media_type not in MEDIA_TYPES:
        return {"success": False, "message": f"Invalid type: {media_type}"}

    type_info = MEDIA_TYPES[media_type]

    # Determine extension based on type
    if media_type == "vim":
        ext = ".mp4"
    elif media_type == "vom":
        ext = ".ogg"
    elif media_type == "mus":
        # Try to get original extension
        ext = ".mp3"
        if message.document:
            for attr in message.document.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    orig_ext = os.path.splitext(attr.file_name)[1].lower()
                    if orig_ext in type_info["extensions"]:
                        ext = orig_ext
                        break
    elif media_type == "vid":
        ext = ".mp4"
    else:
        ext = ".bin"

    # Build file path
    safe_name = "".join(
        c for c in name if c.isalnum() or c in "_-"
    )
    if not safe_name:
        return {"success": False, "message": "Invalid name"}

    file_path = os.path.join(MEDIA_DIR, f"{media_type}_{safe_name}{ext}")

    # Download
    logger.info(
        f"[MEDIA] [{session_name}] Downloading {type_info['label']} "
        f"as '{name}'…"
    )

    try:
        downloaded = await client.download_media(message, file=file_path)
        if not downloaded or not os.path.isfile(file_path):
            return {"success": False, "message": "Download failed"}
    except Exception as e:
        return {"success": False, "message": f"Download error: {e}"}

    file_size = os.path.getsize(file_path)

    # Convert voice message to OGG/Opus if needed
    if media_type == "vom" and not file_path.endswith(".ogg"):
        ogg_path = file_path.rsplit(".", 1)[0] + ".ogg"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", file_path,
                    "-c:a", "libopus", "-b:a", "64k",
                    ogg_path,
                ],
                capture_output=True,
                timeout=60,
            )
            if os.path.isfile(ogg_path):
                os.remove(file_path)
                file_path = ogg_path
                file_size = os.path.getsize(file_path)
        except Exception as e:
            logger.warning(f"[MEDIA] OGG conversion failed: {e}")

    # Convert video message to square MP4 if needed
    if media_type == "vim":
        converted_path = file_path.rsplit(".", 1)[0] + "_round.mp4"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", file_path,
                    "-vf", "crop='min(iw\\,ih)':'min(iw\\,ih)',scale=384:384",
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "128k",
                    "-t", "59",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    converted_path,
                ],
                capture_output=True,
                timeout=120,
            )
            if os.path.isfile(converted_path):
                os.remove(file_path)
                file_path = converted_path
                file_size = os.path.getsize(file_path)
        except Exception as e:
            logger.warning(f"[MEDIA] Video round conversion failed: {e}")

    # Save to registry
    registry = _load_registry()
    key = f"{media_type}:{safe_name}"
    registry[key] = {
        "name": safe_name,
        "type": media_type,
        "file_path": file_path,
        "size": file_size,
        "label": type_info["label"],
    }
    _save_registry(registry)

    logger.info(
        f"[MEDIA] [{session_name}] ✓ Saved {type_info['emoji']} "
        f"'{name}' ({file_size // 1024} KB)"
    )

    return {
        "success": True,
        "message": f"Saved {type_info['label']} '{name}' ({file_size // 1024} KB)",
        "name": safe_name,
        "type": media_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Get / list / delete
# ─────────────────────────────────────────────────────────────────────────────

def get_media(media_type: str, name: str) -> Optional[Dict]:
    """Get a saved media by type and name."""
    registry = _load_registry()
    safe_name = "".join(c for c in name if c.isalnum() or c in "_-")
    key = f"{media_type}:{safe_name}"

    info = registry.get(key)
    if info is None:
        return None

    if not os.path.isfile(info.get("file_path", "")):
        return None

    return info


def list_media(media_type: str = None) -> List[Dict]:
    """List saved media, optionally filtered by type."""
    registry = _load_registry()
    items = []

    for key, info in registry.items():
        if not os.path.isfile(info.get("file_path", "")):
            continue
        if media_type and info.get("type") != media_type:
            continue

        type_info = MEDIA_TYPES.get(info["type"], {})
        items.append({
            "name": info["name"],
            "type": info["type"],
            "label": type_info.get("label", "Unknown"),
            "emoji": type_info.get("emoji", "📎"),
            "size_kb": info.get("size", 0) // 1024,
        })

    return items


def delete_media(media_type: str, name: str) -> Dict:
    """Delete a saved media."""
    registry = _load_registry()
    safe_name = "".join(c for c in name if c.isalnum() or c in "_-")
    key = f"{media_type}:{safe_name}"

    info = registry.get(key)
    if info is None:
        return {"success": False, "message": f"Media '{name}' not found"}

    file_path = info.get("file_path", "")
    if os.path.isfile(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"[MEDIA] File delete failed: {e}")

    del registry[key]
    _save_registry(registry)

    logger.info(f"[MEDIA] Deleted {media_type}:{safe_name}")
    return {"success": True, "message": f"Deleted '{name}'"}


# ─────────────────────────────────────────────────────────────────────────────
# Send media
# ─────────────────────────────────────────────────────────────────────────────

async def send_media(
    client: TelegramClient,
    chat_id,
    media_type: str,
    name: str,
    session_name: str = "Unknown",
    reply_to: int = None,
) -> bool:
    """
    Send a saved media file to a chat.

    Automatically sets voice_note=True for vom, video_note=True for vim.
    """
    info = get_media(media_type, name)
    if info is None:
        logger.error(
            f"[MEDIA] [{session_name}] Media {media_type}:'{name}' not found"
        )
        return False

    file_path = info["file_path"]

    try:
        entity = await client.get_entity(chat_id)
    except Exception as e:
        logger.error(f"[MEDIA] [{session_name}] Can't resolve {chat_id}: {e}")
        return False

    kwargs = {}
    if reply_to:
        kwargs["reply_to"] = reply_to

    try:
        if media_type == "vom":
            # Voice message
            await client.send_file(
                entity,
                file_path,
                voice_note=True,
                **kwargs,
            )
        elif media_type == "vim":
            # Video message (round/circle)
            await client.send_file(
                entity,
                file_path,
                video_note=True,
                **kwargs,
            )
        else:
            # Music or video — normal file send
            await client.send_file(
                entity,
                file_path,
                **kwargs,
            )

        logger.info(
            f"[MEDIA] [{session_name}] ✓ Sent {media_type}:'{name}' "
            f"to {chat_id}"
        )
        return True

    except Exception as e:
        logger.error(
            f"[MEDIA] [{session_name}] Send failed: {type(e).__name__}: {e}"
        )
        return False

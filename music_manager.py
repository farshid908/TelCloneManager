"""
Music manager — handles uploading and listing .m4a files for web player.
"""

import os
import logging
from typing import List, Dict

from config import MUSIC_DIR

logger = logging.getLogger("TG-Auto")

ALLOWED_EXTENSIONS = {".m4a"}
MAX_FILE_SIZE_MB = 50


def ensure_music_dir():
    """Make sure music directory exists."""
    if not os.path.isdir(MUSIC_DIR):
        os.makedirs(MUSIC_DIR, exist_ok=True)
        logger.info(f"[MUSIC] Created directory: {MUSIC_DIR}")


def list_music_files() -> List[Dict]:
    """List all music files with size."""
    ensure_music_dir()
    files = []
    try:
        for fname in sorted(os.listdir(MUSIC_DIR)):
            fpath = os.path.join(MUSIC_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                continue
            files.append({
                "name": fname,
                "size_mb": round(os.path.getsize(fpath) / (1024 * 1024), 2),
            })
    except Exception as e:
        logger.error(f"[MUSIC] Can't list: {e}")
    return files


def save_music_file(filename: str, data: bytes) -> Dict:
    """Save an uploaded music file."""
    ensure_music_dir()

    
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return {
            "success": False,
            "message": f"Only {', '.join(ALLOWED_EXTENSIONS)} files allowed",
        }

    
    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_FILE_SIZE_MB:
        return {
            "success": False,
            "message": f"File too large ({size_mb:.1f} MB, max {MAX_FILE_SIZE_MB} MB)",
        }

    
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")
    if not safe_name.endswith(ext):
        safe_name = safe_name + ext

    fpath = os.path.join(MUSIC_DIR, safe_name)

    try:
        with open(fpath, "wb") as f:
            f.write(data)
        logger.info(f"[MUSIC] Saved: {safe_name} ({size_mb:.2f} MB)")
        return {
            "success": True,
            "message": f"Saved: {safe_name}",
            "name": safe_name,
        }
    except Exception as e:
        logger.error(f"[MUSIC] Save failed: {e}")
        return {"success": False, "message": f"Save failed: {e}"}


def delete_music_file(filename: str) -> Dict:
    """Delete a music file."""
    
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")
    fpath = os.path.join(MUSIC_DIR, safe_name)

    if not os.path.isfile(fpath):
        return {"success": False, "message": "File not found"}

    try:
        os.remove(fpath)
        logger.info(f"[MUSIC] Deleted: {safe_name}")
        return {"success": True, "message": f"Deleted: {safe_name}"}
    except Exception as e:
        logger.error(f"[MUSIC] Delete failed: {e}")
        return {"success": False, "message": f"Delete failed: {e}"}


def get_music_file_path(filename: str):
    """Get full path to a music file (safe)."""
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")
    fpath = os.path.join(MUSIC_DIR, safe_name)
    if os.path.isfile(fpath):
        return fpath
    return None

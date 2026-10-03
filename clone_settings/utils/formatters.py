"""
Text formatting utilities for Clone Manager bot.
"""

__TCM_FILE_HASH__ = "7539206026"


import re
from typing import List, Optional


def escape_md(text: str) -> str:
    """
    Escape markdown special characters for Telegram.

    Note: Telethon's markdown parser is lenient, so we only
    escape the most problematic characters.
    """
    if not text:
        return ""

    
    chars_to_escape = ['_', '*', '`', '[']
    result = text
    for char in chars_to_escape:
        result = result.replace(char, f"\\{char}")
    return result


def truncate(text: str, max_len: int = 50, suffix: str = "…") -> str:
    """Truncate text to max_len, adding suffix if truncated."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


def format_clone_name(name: str, index: int) -> str:
    """Format clone display name with index."""
    return f"#{index} {name}"


def format_status_icon(online: bool) -> str:
    """Get status icon for online/offline."""
    return "🟢" if online else "🔴"


def format_privacy_value(value: str) -> str:
    """Format privacy value for display."""
    mapping = {
        "everyone": "🌐 Everyone",
        "contacts": "👥 Contacts",
        "nobody": "🔒 Nobody",
        "unknown": "❓ Unknown",
    }
    return mapping.get(value, f"❓ {value}")


def format_count(count: int, singular: str, plural: str = None) -> str:
    """Format count with proper singular/plural."""
    if plural is None:
        plural = singular + "s"
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural}"


def format_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_uptime(seconds: float) -> str:
    """Format uptime in human-readable format."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"
    else:
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        return f"{days}d {hours}h"


def format_results_summary(results: dict) -> str:
    """Format a results dict into a summary string."""
    parts = []

    if "success" in results:
        parts.append(f"✓ {results['success']}")
    if "failed" in results:
        parts.append(f"✗ {results['failed']}")
    if "skipped" in results:
        parts.append(f"⏭ {results['skipped']}")
    if "already" in results:
        parts.append(f"⏭ {results['already']}")
    if "flood" in results:
        parts.append(f"⏳ {results['flood']}")

    return " | ".join(parts) if parts else "—"


def build_progress_bar(current: int, total: int, width: int = 20) -> str:
    """Build a text-based progress bar."""
    if total <= 0:
        return f"[{'?' * width}]"

    filled = int(width * current / total)
    empty = width - filled

    bar = "█" * filled + "░" * empty
    percent = int(100 * current / total)

    return f"[{bar}] {percent}%"


def format_clone_list_entry(
    index: int,
    name: str,
    online: bool,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
) -> str:
    """Format a single clone list entry."""
    icon = format_status_icon(online)
    display_name = first_name or name
    uname = f"@{username}" if username else "—"

    return f"{icon} **#{index}** {display_name} — {uname}"

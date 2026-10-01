"""
Analytics menu — stats about bot usage.
"""

import time
import os

from ..utils.keyboards import make_inline_keyboard


def build_analytics_menu(automation):
    """Build analytics and statistics menu."""

    total_clones = len(automation.clone_clients)
    online = sum(1 for c in automation.clone_clients if c.is_connected())
    active_loops = len(automation.loops.active)
    mirror = automation.mirror_mode

    # Process uptime
    try:
        import psutil
        process = psutil.Process(os.getpid())
        create_time = process.create_time()
        uptime_seconds = time.time() - create_time

        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{hours}h {minutes}m"

        # Memory
        mem_info = process.memory_info()
        ram_mb = mem_info.rss / (1024 * 1024)
        ram_str = f"{ram_mb:.1f} MB"

        # CPU
        cpu_percent = process.cpu_percent(interval=0.1)
        cpu_str = f"{cpu_percent:.1f}%"
    except ImportError:
        uptime_str = "—"
        ram_str = "—"
        cpu_str = "—"
    except Exception:
        uptime_str = "—"
        ram_str = "—"
        cpu_str = "—"

    # Disk usage (sessions dir)
    try:
        from config import SESSIONS_DIR
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(SESSIONS_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                except Exception:
                    pass
        disk_str = f"{total_size / (1024 * 1024):.1f} MB"
    except Exception:
        disk_str = "—"

    # State persistence stats
    try:
        from state_persistence import get_stats
        stats = get_stats()
        state_loops = stats.get("active_loops", 0)
        state_seq = stats.get("pending_sequential", 0)
        state_hunts = stats.get("active_hunt_clicks", 0)
    except Exception:
        state_loops = "—"
        state_seq = "—"
        state_hunts = "—"

    text = (
        "📈 **Analytics**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"**📊 Current State:**\n"
        f"  Clones online: {online}/{total_clones}\n"
        f"  Active loops: {active_loops}\n"
        f"  Mirror mode: {'ON ✅' if mirror else 'OFF ❌'}\n\n"
        f"**💾 State Persistence:**\n"
        f"  Saved loops: {state_loops}\n"
        f"  Pending sequential: {state_seq}\n"
        f"  Active hunt-clicks: {state_hunts}\n\n"
        f"**🖥️ System Resources:**\n"
        f"  Uptime: {uptime_str}\n"
        f"  RAM: {ram_str}\n"
        f"  CPU: {cpu_str}\n"
        f"  Disk (sessions): {disk_str}\n\n"
        f"**🆔 Process:**\n"
        f"  PID: `{os.getpid()}`\n"
    )

    buttons = [
        [
            ("🔄 Refresh", "menu:analytics"),
        ],
        [
            ("📊 Status", "menu:status"),
            ("🛡️ Safety", "menu:safety"),
        ],
        [
            ("🏠 Main Menu", "menu:main"),
        ],
    ]

    keyboard = make_inline_keyboard(buttons)
    return text, keyboard

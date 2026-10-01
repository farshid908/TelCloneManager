"""Persist bot power heartbeats and report downtime after a restart."""

import asyncio
import logging
import os
from datetime import datetime

logger = logging.getLogger("TG-Auto")

POWER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "power")
HEARTBEAT_SECONDS = 60

_task = None


def _now() -> datetime:
    return datetime.now().astimezone()


def _format_dt(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return "unknown"


def _read_last_state():
    if not os.path.exists(POWER_FILE):
        return None, None
    timestamp = None
    boot_id = None
    try:
        with open(POWER_FILE, "r", encoding="utf-8") as stream:
            lines = stream.readlines()
        for line in reversed(lines):
            if line.startswith("BOOT_ID ") and boot_id is None:
                boot_id = line.split(" ", 1)[1].strip()
            elif line.startswith(("STARTED ", "HEARTBEAT ")) and timestamp is None:
                raw = line.split(" ", 1)[1].strip()
                timestamp = datetime.fromisoformat(raw)
            if timestamp is not None and boot_id is not None:
                break
    except Exception:
        logger.warning("[POWER] Could not read previous heartbeat", exc_info=True)
    return timestamp, boot_id


def _append(line: str):
    os.makedirs(os.path.dirname(POWER_FILE), exist_ok=True)
    with open(POWER_FILE, "a", encoding="utf-8") as stream:
        stream.write(line + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _duration_text(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes or not parts:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    return ", ".join(parts)


async def _heartbeat_loop():
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        try:
            _append(f"HEARTBEAT {_format_dt(_now())}")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[POWER] Heartbeat write failed", exc_info=True)


async def start_power_monitor(bot, admin_id):
    """Record startup, report detected downtime, and start minute heartbeats."""
    global _task

    if _task is not None and not _task.done():
        return

    started_at = _now()
    current_boot_id = _boot_id()
    previous, previous_boot_id = _read_last_state()
    server_rebooted = (
        previous is not None
        and previous_boot_id is not None
        and previous_boot_id != current_boot_id
    )
    if previous is None:
        _append(f"STARTED {_format_dt(started_at)}")
        _append(f"BOOT_ID {current_boot_id}")
        logger.info("[POWER] Monitor initialized")
    else:
        _append(f"RESTARTED {_format_dt(started_at)}")
        _append(f"BOOT_ID {current_boot_id}")
        if server_rebooted:
            downtime = max(0.0, (started_at - previous).total_seconds())
            _append(f"OFFLINE_FROM {_format_dt(previous)}")
            _append(f"OFFLINE_TO {_format_dt(started_at)}")
            _append(f"DOWNTIME {_duration_text(downtime)}")
            logger.info(
                "[POWER] Detected server reboot downtime: %s -> %s (%s)",
                _format_dt(previous), _format_dt(started_at),
                _duration_text(downtime),
            )
            if bot is not None and admin_id is not None:
                message = (
                    "Power report\n\n"
                    f"The server was offline from {previous.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                    f"and came back online at {started_at.strftime('%Y-%m-%d %H:%M:%S %Z')}.\n\n"
                    f"Total downtime: {_duration_text(downtime)}."
                )
                try:
                    await bot.send_message(admin_id, message)
                except Exception:
                    logger.warning("[POWER] Could not send admin notification", exc_info=True)
        else:
            logger.info("[POWER] Process restart detected without a server reboot")

    _append(f"HEARTBEAT {_format_dt(started_at)}")
    _task = asyncio.create_task(_heartbeat_loop(), name="power-heartbeat")


async def stop_power_monitor():
    """Stop heartbeat writes without adding a fake clean shutdown timestamp."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None

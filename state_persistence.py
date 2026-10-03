"""
State persistence for the Telegram bot.

Saves running loops, sequential sends, and hunt-clicks to disk
so they can be resumed after a restart (Ctrl+C, crash, or manual reload).

State file: sessions/_runtime_state.json
"""

__TCM_FILE_HASH__ = "8001344640"


import os
import json
import time
import tempfile
import logging
from typing import Dict, List, Optional
from threading import Lock

logger = logging.getLogger("TG-Auto")

STATE_FILE = "sessions/_runtime_state.json"

_state_lock = Lock()
_UNSET = object()
_current_state = {
    "loops": {},
    "sequential": {},
    "hunt_clicks": {},
    "rep_jobs": {},
}






def load_state_from_disk() -> Dict:
    """Load state from disk. Returns empty state on error/missing."""
    if not os.path.exists(STATE_FILE):
        return {
            "loops": {},
            "sequential": {},
            "hunt_clicks": {},
            "rep_jobs": {},
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("loops", {})
        data.setdefault("sequential", {})
        data.setdefault("hunt_clicks", {})
        data.setdefault("rep_jobs", {})
        
        
        
        
        for info in data["loops"].values():
            if info.get("iteration") is None:
                info["iteration"] = 0
            if info.get("next_sender_index") is None:
                info["next_sender_index"] = 0
            if "current_sender_index" not in info:
                info["current_sender_index"] = None
            if "current_sender_name" not in info:
                info["current_sender_name"] = None
            if info.get("phase") is None:
                info["phase"] = "scheduled"
            if "last_sent_message_id" not in info:
                info["last_sent_message_id"] = None
            if "last_reply_message_id" not in info:
                info["last_reply_message_id"] = None
            if "last_action" not in info:
                info["last_action"] = None
            if "checkpoint_at" not in info:
                info["checkpoint_at"] = info.get("started_at", time.time())
        logger.info(
            f"[STATE] Loaded from disk: {len(data['loops'])} loop(s), "
            f"{len(data['sequential'])} sequential send(s), "
            f"{len(data['hunt_clicks'])} hunt-click(s), "
            f"{len(data['rep_jobs'])} REP job(s)"
        )
        return data
    except Exception as e:
        logger.error(f"[STATE] Load failed: {e}")
        return {
            "loops": {},
            "sequential": {},
            "hunt_clicks": {},
            "rep_jobs": {},
        }


def save_state_to_disk():
    """Save current in-memory state to disk (thread-safe)."""
    try:
        state_dir = os.path.dirname(STATE_FILE) or "."
        os.makedirs(state_dir, exist_ok=True)
        with _state_lock:
            fd, temporary = tempfile.mkstemp(
                prefix=".runtime_state.", suffix=".tmp", dir=state_dir,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(_current_state, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, STATE_FILE)
                try:
                    dir_fd = os.open(state_dir, os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    except Exception as e:
        logger.error(f"[STATE] Save failed: {e}")


def init_state():
    """Initialize state from disk on startup."""
    global _current_state
    with _state_lock:
        _current_state = load_state_from_disk()
    
    
    save_state_to_disk()






def make_loop_id(chat_id: int, text: str) -> str:
    return f"{chat_id}:{text}"


def register_loop(chat_id: int, text: str, params: Dict) -> str:
    """Register a new loop in state."""
    loop_id = make_loop_id(chat_id, text)
    now = time.time()

    with _state_lock:
        _current_state["loops"][loop_id] = {
            "chat_id": chat_id,
            "text": text,
            "params": params,
            "started_at": now,
            "last_iteration_at": now,
            "next_iteration_at": now + params.get("loop_interval", 0),
            "iteration": 0,
            "next_sender_index": 0,
            "current_sender_index": None,
            "current_sender_name": None,
            "phase": "scheduled",
            "last_sent_message_id": None,
            "last_reply_message_id": None,
            "last_action": None,
        }

    save_state_to_disk()
    logger.info(f"[STATE] Registered loop: {loop_id}")
    return loop_id


def update_loop_iteration(chat_id: int, text: str):
    """Mark that a loop iteration just completed."""
    loop_id = make_loop_id(chat_id, text)
    now = time.time()

    with _state_lock:
        if loop_id in _current_state["loops"]:
            info = _current_state["loops"][loop_id]
            info["last_iteration_at"] = now
            info["next_iteration_at"] = now + info["params"].get("loop_interval", 0)

    save_state_to_disk()


def update_loop_progress(
    chat_id: int,
    text: str,
    *,
    iteration=_UNSET,
    next_sender_index=_UNSET,
    current_sender_index=_UNSET,
    current_sender_name=_UNSET,
    phase=_UNSET,
    last_sent_message_id=_UNSET,
    last_reply_message_id=_UNSET,
    last_action=_UNSET,
    next_iteration_at=_UNSET,
):
    """Persist a precise checkpoint for a running loop."""
    loop_id = make_loop_id(chat_id, text)
    with _state_lock:
        info = _current_state["loops"].get(loop_id)
        if info is None:
            return
        values = {
            "iteration": iteration,
            "next_sender_index": next_sender_index,
            "current_sender_index": current_sender_index,
            "current_sender_name": current_sender_name,
            "phase": phase,
            "last_sent_message_id": last_sent_message_id,
            "last_reply_message_id": last_reply_message_id,
            "last_action": last_action,
            "next_iteration_at": next_iteration_at,
        }
        for key, value in values.items():
            if value is not _UNSET:
                info[key] = value
        info["checkpoint_at"] = time.time()
    save_state_to_disk()


def unregister_loop(chat_id: int, text: str):
    """Remove a loop from state (when user stops it explicitly)."""
    loop_id = make_loop_id(chat_id, text)

    with _state_lock:
        if loop_id in _current_state["loops"]:
            del _current_state["loops"][loop_id]

    save_state_to_disk()
    logger.info(f"[STATE] Unregistered loop: {loop_id}")


def get_all_loops() -> Dict:
    """Get all registered loops."""
    with _state_lock:
        return dict(_current_state["loops"])






def register_sequential(
    chat_id: int,
    text: str,
    params: Dict,
    total_senders: int,
) -> str:
    """Register a sequential (delayed) send."""
    seq_id = f"{chat_id}:{text}:{int(time.time() * 1000)}"
    now = time.time()

    with _state_lock:
        _current_state["sequential"][seq_id] = {
            "chat_id": chat_id,
            "text": text,
            "params": params,
            "total_senders": total_senders,
            "sent_count": 0,
            "sent_senders": [],
            "started_at": now,
            "next_send_at": now,
        }

    save_state_to_disk()
    logger.info(f"[STATE] Registered sequential: {seq_id}")
    return seq_id


def update_sequential_progress(
    seq_id: str,
    sender_name: str,
    delay_after: int = 0,
):
    """Update progress of a sequential send."""
    now = time.time()

    with _state_lock:
        if seq_id in _current_state["sequential"]:
            info = _current_state["sequential"][seq_id]
            info["sent_count"] += 1
            if sender_name not in info["sent_senders"]:
                info["sent_senders"].append(sender_name)
            info["next_send_at"] = now + delay_after

    save_state_to_disk()


def unregister_sequential(seq_id: str):
    """Remove a sequential send from state (when complete)."""
    with _state_lock:
        if seq_id in _current_state["sequential"]:
            del _current_state["sequential"][seq_id]

    save_state_to_disk()
    logger.debug(f"[STATE] Unregistered sequential: {seq_id}")


def get_all_sequentials() -> Dict:
    """Get all registered sequential sends."""
    with _state_lock:
        return dict(_current_state["sequential"])






def register_hunt_click(
    session_ref: str,
    clone_index: int,
    times: int,
    button_text: str,
    delay: int,
    done: int = 0,
    edit_mode: bool = False,
    loop_mode: bool = False,
    disappear_mode: bool = False,
    chat_id: Optional[int] = None,
    cursor: int = 0,
    kind: str = "hunt",
) -> str:
    """Register a hunt-click task."""
    task_id = f"{session_ref}:{button_text}:{int(time.time() * 1000)}"
    now = time.time()

    with _state_lock:
        _current_state["hunt_clicks"][task_id] = {
            "session_ref": session_ref,
            "clone_index": clone_index,
            "times": times,
            "button_text": button_text,
            "delay": delay,
            "done": done,
            "edit_mode": edit_mode,
            "loop_mode": loop_mode,
            "disappear_mode": disappear_mode,
            "chat_id": chat_id,
            "cursor": cursor,
            "kind": kind,
            "started_at": now,
            "next_click_at": now,
        }

    save_state_to_disk()
    logger.info(f"[STATE] Registered hunt-click: {task_id}")
    return task_id

def update_hunt_click_progress(task_id: str, done: int, delay: int):
    """Update progress of a hunt-click task after each click."""
    now = time.time()

    with _state_lock:
        if task_id in _current_state["hunt_clicks"]:
            info = _current_state["hunt_clicks"][task_id]
            info["done"] = done
            info["next_click_at"] = now + delay

    save_state_to_disk()


def update_hunt_click_cursor(task_id: str, cursor: int):
    """Persist the active account/cursor for a persistent click task."""
    with _state_lock:
        if task_id in _current_state["hunt_clicks"]:
            _current_state["hunt_clicks"][task_id]["cursor"] = cursor
    save_state_to_disk()


def unregister_hunt_click(task_id: str):
    """Remove a hunt-click task from state."""
    with _state_lock:
        if task_id in _current_state["hunt_clicks"]:
            del _current_state["hunt_clicks"][task_id]

    save_state_to_disk()
    logger.debug(f"[STATE] Unregistered hunt-click: {task_id}")


def get_all_hunt_clicks() -> Dict:
    """Get all registered hunt-click tasks."""
    with _state_lock:
        return dict(_current_state["hunt_clicks"])






def register_rep_job(job_id: str, info: Dict) -> str:
    """Register a REP job with a complete JSON-serializable checkpoint."""
    checkpoint = dict(info)
    checkpoint.setdefault("started_at", time.time())
    checkpoint["checkpoint_at"] = time.time()
    with _state_lock:
        _current_state["rep_jobs"][str(job_id)] = checkpoint
    save_state_to_disk()
    logger.info("[STATE] Registered REP job: %s", job_id)
    return str(job_id)


def update_rep_job(job_id: str, **changes):
    """Atomically update selected fields of a REP checkpoint."""
    key = str(job_id)
    with _state_lock:
        info = _current_state["rep_jobs"].get(key)
        if info is None:
            return
        info.update(changes)
        info["checkpoint_at"] = time.time()
    save_state_to_disk()


def unregister_rep_job(job_id: str):
    """Remove a REP checkpoint after stop or normal completion."""
    key = str(job_id)
    with _state_lock:
        _current_state["rep_jobs"].pop(key, None)
    save_state_to_disk()
    logger.info("[STATE] Unregistered REP job: %s", job_id)


def get_all_rep_jobs() -> Dict:
    """Return persisted REP checkpoints without exposing internal state."""
    with _state_lock:
        return {
            key: dict(value)
            for key, value in _current_state["rep_jobs"].items()
        }






def cleanup_stale_state(max_age_hours: int = 24):
    """Remove stale sequential/hunt-click entries older than max_age_hours."""
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    total_cleaned = 0

    with _state_lock:
        
        to_remove = []
        for seq_id, info in _current_state["sequential"].items():
            if info.get("started_at", 0) < cutoff:
                to_remove.append(seq_id)
        for seq_id in to_remove:
            del _current_state["sequential"][seq_id]
        if to_remove:
            logger.info(f"[STATE] Cleaned {len(to_remove)} stale sequential(s)")
            total_cleaned += len(to_remove)

        
        to_remove = []
        for task_id, info in _current_state["hunt_clicks"].items():
            
            if (info.get("edit_mode")
                or info.get("loop_mode")
                or info.get("disappear_mode")):
                continue
            if info.get("started_at", 0) < cutoff:
                to_remove.append(task_id)
        for task_id in to_remove:
            del _current_state["hunt_clicks"][task_id]
        if to_remove:
            logger.info(f"[STATE] Cleaned {len(to_remove)} stale hunt-click(s)")
            total_cleaned += len(to_remove)

    if total_cleaned > 0:
        save_state_to_disk()

def get_stats() -> Dict:
    """Get current stats about state."""
    with _state_lock:
        return {
            "active_loops": len(_current_state["loops"]),
            "pending_sequential": len(_current_state["sequential"]),
            "active_hunt_clicks": len(_current_state["hunt_clicks"]),
            "active_rep_jobs": len(_current_state["rep_jobs"]),
        }


def clear_all_state():
    """Clear ALL state (for debugging or manual reset)."""
    global _current_state
    with _state_lock:
        _current_state = {
            "loops": {},
            "sequential": {},
            "hunt_clicks": {},
            "rep_jobs": {},
        }
    save_state_to_disk()
    logger.warning("[STATE] All state cleared!")

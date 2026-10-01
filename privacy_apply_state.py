"""Persistent record of privacy policies already applied."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


STATE_PATH = Path(__file__).resolve().parent / "privacy_apply_state.json"


def _default():
    return {
        "clones": {},
        "main_photo_targets": {"users": [], "chats": []},
    }


def load_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = _default()
    if not isinstance(data, dict):
        data = _default()
    data.setdefault("clones", {})
    data.setdefault("main_photo_targets", {"users": [], "chats": []})
    data["main_photo_targets"].setdefault("users", [])
    data["main_photo_targets"].setdefault("chats", [])
    return data


def save_state(data):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="privacy-state.", suffix=".tmp", dir=STATE_PATH.parent
    )
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        Path(temp_name).replace(STATE_PATH)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def clone_policy_applied(session_name, key_name):
    state = load_state()
    return key_name in state["clones"].get(session_name, [])


def mark_clone_policy(session_name, key_name):
    state = load_state()
    keys = state["clones"].setdefault(session_name, [])
    if key_name not in keys:
        keys.append(key_name)
        save_state(state)


def clear_clone_policy(session_name):
    """Force all default policies to be re-evaluated for one clone."""
    state = load_state()
    if session_name in state["clones"]:
        state["clones"].pop(session_name, None)
        save_state(state)


def mark_main_photo_targets(user_ids=None, chat_ids=None):
    state = load_state()
    targets = state["main_photo_targets"]
    for value in user_ids or []:
        value = int(value)
        if value not in targets["users"]:
            targets["users"].append(value)
    for value in chat_ids or []:
        value = int(value)
        if value not in targets["chats"]:
            targets["chats"].append(value)
    save_state(state)


def forget_main_photo_targets(user_ids=None, chat_ids=None):
    state = load_state()
    targets = state["main_photo_targets"]
    users = {int(value) for value in user_ids or []}
    chats = {int(value) for value in chat_ids or []}
    targets["users"] = [
        value for value in targets["users"] if int(value) not in users
    ]
    targets["chats"] = [
        value for value in targets["chats"] if int(value) not in chats
    ]
    save_state(state)


def unmarked_main_photo_targets(user_ids=None, chat_ids=None):
    state = load_state()
    targets = state["main_photo_targets"]
    marked_users = {int(value) for value in targets["users"]}
    marked_chats = {int(value) for value in targets["chats"]}
    return (
        [int(value) for value in user_ids or [] if int(value) not in marked_users],
        [int(value) for value in chat_ids or [] if int(value) not in marked_chats],
    )

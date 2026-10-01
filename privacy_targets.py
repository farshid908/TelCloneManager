"""Persistent profile-visibility targets shared by all clones."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


STORE_PATH = Path(__file__).resolve().parent / "privacy_targets.json"


def _default():
    return {"users": {}, "chats": {}}


def load_targets():
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = _default()
    if not isinstance(data, dict):
        data = _default()
    data.setdefault("users", {})
    data.setdefault("chats", {})
    return data


def save_targets(data):
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="privacy-targets.", suffix=".tmp", dir=STORE_PATH.parent
    )
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        Path(temp_name).replace(STORE_PATH)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def remember_targets(user_ids=None, chat_ids=None, metadata=None):
    data = load_targets()
    metadata = metadata or {}
    for value in user_ids or []:
        key = str(int(value))
        data["users"][key] = dict(metadata.get(("user", int(value)), {}))
    for value in chat_ids or []:
        key = str(int(value))
        data["chats"][key] = dict(metadata.get(("chat", int(value)), {}))
    save_targets(data)


def forget_targets(user_ids=None, chat_ids=None):
    data = load_targets()
    for value in user_ids or []:
        data["users"].pop(str(int(value)), None)
    for value in chat_ids or []:
        data["chats"].pop(str(int(value)), None)
    save_targets(data)


def allowed_ids():
    data = load_targets()
    return (
        [int(value) for value in data["users"]],
        [int(value) for value in data["chats"]],
    )

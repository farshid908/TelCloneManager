"""Persistent access control for the Normal Mode menu."""

import json
import os
from pathlib import Path


ACCESS_FILE = Path(__file__).resolve().parent / "normal_menu_access.json"


def _load():
    try:
        data = json.loads(ACCESS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data):
    ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = ACCESS_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temp_file, ACCESS_FILE)


def grant(user_id):
    return grant_with_info(user_id)


def grant_with_info(user_id, info=None):
    data = _load()
    data = data if isinstance(data, dict) else {}
    current = get_access()
    if current is not None and str(current["user_id"]) != str(user_id):
        return False
    data["active"] = {
        "user_id": int(user_id),
        **(info or {}),
    }
    _save(data)
    return True


def revoke(user_id):
    data = _load()
    current = data.get("active")
    if isinstance(current, dict):
        if str(current.get("user_id")) != str(user_id):
            return False
        data.pop("active", None)
    # Remove legacy per-user records too. Otherwise get_access() can
    # immediately restore access from an old record after active is removed.
    data.pop(str(user_id), None)
    _save(data)
    return True


def has_access(user_id):
    current = get_access()
    return bool(
        user_id is not None
        and current is not None
        and str(current["user_id"]) == str(user_id)
    )


def get_access():
    data = _load()
    current = data.get("active")
    if isinstance(current, dict) and current.get("user_id") is not None:
        return current
    for user_id, value in data.items():
        if value is True:
            return {"user_id": int(user_id)}
    return None

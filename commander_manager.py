"""Persistent commander session registry and ownership switching."""

__TCM_FILE_HASH__ = "2809533667"


import json
import os
import re
import shutil
from pathlib import Path

from config import SESSIONS_DIR

STATE_FILE = Path(SESSIONS_DIR) / "_commander_state.json"
PRIMARY_NAME = "main_commander"
COMMANDER_PATTERN = re.compile(r"commander(\d+)$", re.IGNORECASE)


def _save(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, ensure_ascii=False)
    os.replace(temporary, STATE_FILE)


def load_state():
    if STATE_FILE.is_file():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as file:
                state = json.load(file)
        except (OSError, ValueError):
            state = {}
    else:
        state = {}
    state.setdefault("primary", PRIMARY_NAME)
    state.setdefault("active", PRIMARY_NAME)
    state.setdefault("ids", {})
    return state


def migrate_legacy_names():
    """Migrate Main/MainN files to the commander naming scheme once."""
    root = Path(SESSIONS_DIR)
    pending = root / "_pending"
    root.mkdir(parents=True, exist_ok=True)
    pending.mkdir(parents=True, exist_ok=True)
    mappings = {}
    for directory in (root, pending):
        main_path = directory / "Main.session"
        mappings[main_path] = directory / f"{PRIMARY_NAME}.session"
        for path in directory.glob("Main*.session"):
            match = re.fullmatch(r"Main(\d+)\.session", path.name, re.IGNORECASE)
            if match:
                number = max(int(match.group(1)) - 1, 1)
                mappings[path] = directory / f"commander{number}.session"

    for source, destination in mappings.items():
        if not source.exists() or destination.exists():
            continue
        shutil.move(str(source), str(destination))
        source_json = source.with_suffix(".json")
        destination_json = destination.with_suffix(".json")
        if source_json.exists() and not destination_json.exists():
            shutil.move(str(source_json), str(destination_json))

    state = load_state()
    legacy_active = state.get("active")
    if legacy_active and legacy_active.lower() == "main":
        state["active"] = PRIMARY_NAME
    if state.get("primary", "").lower() == "main":
        state["primary"] = PRIMARY_NAME
    _save(state)
    return state


def ensure_migrated():
    return migrate_legacy_names()


def list_commanders():
    migrate_legacy_names()
    root = Path(SESSIONS_DIR)
    names = []
    directories = (root, root / "_pending")
    if any((directory / f"{PRIMARY_NAME}.session").is_file() for directory in directories):
        names.append(PRIMARY_NAME)
    for directory in directories:
        for path in directory.glob("commander*.session"):
            name = path.stem
            if COMMANDER_PATTERN.fullmatch(name):
                names.append(name)
    return sorted(
        set(names),
        key=lambda name: (0, 0) if name == PRIMARY_NAME else (
            1, int(COMMANDER_PATTERN.fullmatch(name).group(1))
        ),
    )


def active_name():
    state = migrate_legacy_names()
    available = list_commanders()
    active = state.get("active", PRIMARY_NAME)
    return active if active in available else (
        PRIMARY_NAME if PRIMARY_NAME in available else (available[0] if available else None)
    )


def path_for(name):
    active_path = Path(SESSIONS_DIR) / f"{name}.session"
    pending_path = Path(SESSIONS_DIR) / "_pending" / f"{name}.session"
    return active_path if active_path.exists() else pending_path


def is_primary(user_id):
    state = load_state()
    return str(user_id) == str(state.get("ids", {}).get(state["primary"]))


def is_active(user_id):
    state = load_state()
    active = state.get("active", PRIMARY_NAME)
    return str(user_id) == str(state.get("ids", {}).get(active))


def identity_ids():
    state = load_state()
    values = {}
    for name in list_commanders():
        value = state.get("ids", {}).get(name)
        if value is not None:
            values[name] = int(value)
    return values


def register_identity(name, user_id):
    state = load_state()
    state.setdefault("ids", {})[name] = int(user_id)
    if name == state.get("primary") and not state["ids"].get(state["primary"]):
        state["ids"][state["primary"]] = int(user_id)
    _save(state)


def set_active(name):
    available = list_commanders()
    if name not in available:
        return {"success": False, "message": f"Commander '{name}' not found"}
    state = load_state()
    current = active_name()
    if current == name:
        return {"success": True, "message": f"Commander '{name}' is already active."}

    root = Path(SESSIONS_DIR)
    pending = root / "_pending"
    current_path = path_for(current) if current else None
    new_path = path_for(name)
    try:
        if current_path and current_path.parent == root:
            shutil.move(str(current_path), str(pending / current_path.name))
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(current_path) + suffix)
                if sidecar.exists():
                    shutil.move(str(sidecar), str(pending / sidecar.name))
        if new_path.parent == pending:
            shutil.move(str(new_path), str(root / new_path.name))
            for suffix in ("-journal", "-wal", "-shm"):
                sidecar = Path(str(new_path) + suffix)
                if sidecar.exists():
                    shutil.move(str(sidecar), str(root / sidecar.name))
    except OSError as exc:
        return {"success": False, "message": f"Commander switch failed: {exc}"}

    state["active"] = name
    _save(state)
    return {
        "success": True,
        "message": f"Commander switched to {name}. Reload is required.",
    }


def is_commander_name(name):
    return (
        str(name).lower() == PRIMARY_NAME.lower()
        or bool(COMMANDER_PATTERN.fullmatch(str(name)))
    )


def display_name(name):
    if name == PRIMARY_NAME:
        return "main commander"
    match = COMMANDER_PATTERN.fullmatch(name)
    return f"Commander{int(match.group(1))}" if match else name

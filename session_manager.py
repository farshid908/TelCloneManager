"""
Session management module.
Handles:
  - Creating new Main and Clone sessions (with 2FA support)
  - Setting active Main session
  - Converting between Main ↔ Clone
  - Samsung device profiles
  - Bridge group invite link
  - Phone number registry (prevent duplicates)
  - Slot management (reuse deleted numbers)
"""

import os
import json
import random
import asyncio
import logging
import shutil
import re
import threading
from typing import Dict, List, Optional

from telethon import TelegramClient
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import (
    UserAlreadyParticipantError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    FloodWaitError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
)

from config import (
    API_ID, API_HASH,
    SESSIONS_DIR, MAIN_SESSION_NAME, BRIDGE_INVITE_LINK,
)

logger = logging.getLogger("TG-Auto")


# ─────────────────────────────────────────────────────────────────────────────
# Samsung device profiles
# ─────────────────────────────────────────────────────────────────────────────

SAMSUNG_DEVICES = [
    {"model": "SM-S928B",  "name": "Samsung Galaxy S24 Ultra"},
    {"model": "SM-S926B",  "name": "Samsung Galaxy S24+"},
    {"model": "SM-S921B",  "name": "Samsung Galaxy S24"},
    {"model": "SM-S918B",  "name": "Samsung Galaxy S23 Ultra"},
    {"model": "SM-S916B",  "name": "Samsung Galaxy S23+"},
    {"model": "SM-S911B",  "name": "Samsung Galaxy S23"},
    {"model": "SM-S908B",  "name": "Samsung Galaxy S22 Ultra"},
    {"model": "SM-S906B",  "name": "Samsung Galaxy S22+"},
    {"model": "SM-S901B",  "name": "Samsung Galaxy S22"},
    {"model": "SM-G998B",  "name": "Samsung Galaxy S21 Ultra"},
    {"model": "SM-G996B",  "name": "Samsung Galaxy S21+"},
    {"model": "SM-G991B",  "name": "Samsung Galaxy S21"},
    {"model": "SM-G988B",  "name": "Samsung Galaxy S20 Ultra"},
    {"model": "SM-G986B",  "name": "Samsung Galaxy S20+"},
    {"model": "SM-G981B",  "name": "Samsung Galaxy S20"},
    {"model": "SM-N986B",  "name": "Samsung Galaxy Note 20 Ultra"},
    {"model": "SM-N981B",  "name": "Samsung Galaxy Note 20"},
    {"model": "SM-F946B",  "name": "Samsung Galaxy Z Fold 5"},
    {"model": "SM-F731B",  "name": "Samsung Galaxy Z Flip 5"},
    {"model": "SM-F936B",  "name": "Samsung Galaxy Z Fold 4"},
    {"model": "SM-F721B",  "name": "Samsung Galaxy Z Flip 4"},
    {"model": "SM-A546B",  "name": "Samsung Galaxy A54 5G"},
    {"model": "SM-A536B",  "name": "Samsung Galaxy A53 5G"},
    {"model": "SM-A346B",  "name": "Samsung Galaxy A34 5G"},
    {"model": "SM-A146B",  "name": "Samsung Galaxy A14 5G"},
    {"model": "SM-A736B",  "name": "Samsung Galaxy A73"},
    {"model": "SM-A526B",  "name": "Samsung Galaxy A52"},
    {"model": "SM-A725F",  "name": "Samsung Galaxy A72"},
]

APP_VERSION = "1.0.0"
SYSTEM_VERSION = "SDK 34"
LANG_CODE = "en"
SYSTEM_LANG_CODE = "en-US"


def get_random_samsung_device() -> Dict[str, str]:
    return random.choice(SAMSUNG_DEVICES)


# ─────────────────────────────────────────────────────────────────────────────
# State file
# ─────────────────────────────────────────────────────────────────────────────

STATE_FILE = os.path.join(SESSIONS_DIR, "_state.json")
REPAIR_STATE_FILE = os.path.join(SESSIONS_DIR, "_repair_state.json")
PENDING_DIR = os.path.join(SESSIONS_DIR, "_pending")
SESSION_INFO_DIR = SESSIONS_DIR


def _ensure_dirs():
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(PENDING_DIR, exist_ok=True)


def load_state() -> Dict:
    """Load session state."""
    _ensure_dirs()
    if not os.path.exists(STATE_FILE):
        return {
            "active_main": MAIN_SESSION_NAME,
            "bridge_invite_link": "",
            "used_phones": {},
            "repair_ran": False,
            "invalid_sessions": [],
        }
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if str(data.get("active_main", "")).lower() == "main":
            data["active_main"] = MAIN_SESSION_NAME
        data.setdefault("active_main", MAIN_SESSION_NAME)
        data.setdefault("bridge_invite_link", "")
        data.setdefault("used_phones", {})
        data.setdefault("repair_ran", False)
        data.setdefault("invalid_sessions", [])
        return data
    except Exception:
        return {
            "active_main": MAIN_SESSION_NAME,
            "bridge_invite_link": "",
            "used_phones": {},
            "repair_ran": False,
            "invalid_sessions": [],
        }


def save_state(state: Dict):
    _ensure_dirs()
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[SESSION] Failed to save state: {e}")


def _save_repair_state(state: Dict):
    """Atomically persist the repair plan and its current step."""
    _ensure_dirs()
    temporary = f"{REPAIR_STATE_FILE}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, REPAIR_STATE_FILE)
    except Exception as exc:
        logger.error("[REPAIR] Failed to persist repair state: %s", exc)
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
        raise


def _load_repair_state() -> Optional[Dict]:
    if not os.path.isfile(REPAIR_STATE_FILE):
        return None
    try:
        with open(REPAIR_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("[REPAIR] Cannot read repair state: %s", exc)
        return None


def _clear_repair_state():
    try:
        if os.path.exists(REPAIR_STATE_FILE):
            os.remove(REPAIR_STATE_FILE)
    except OSError as exc:
        logger.error("[REPAIR] Cannot clear repair state: %s", exc)


async def _execute_repair_plan(plan: Dict, progress_callback=None) -> Dict:
    """Resume a persisted repair plan safely and idempotently."""
    invalid = plan.get("invalid", [])
    valid_active = plan.get("valid_active", [])
    assignments = {
        int(old): int(new)
        for old, new in plan.get("assignments", {}).items()
    }
    old_to_new = plan.get("old_to_new", {})
    phase = plan.get("phase", "delete_invalid")
    total = max(len(valid_active) + len(invalid), 1)

    async def report(index, name, current_phase, completed):
        if progress_callback is None:
            return
        result = progress_callback(index, name, current_phase, completed, total)
        if asyncio.iscoroutine(result):
            await result

    if phase == "delete_invalid":
        for index, item in enumerate(invalid, start=1):
            _remove_session_files(item["path"])
            delete_session_info(item["name"])
            await report(index, item["name"], "failed", index)
        plan["phase"] = "stage"
        _save_repair_state(plan)

    if plan.get("phase") == "stage":
        staged = plan.setdefault("staged", {})
        for item in valid_active:
            match = re.search(r"(\d+)$", item["name"])
            if not match:
                continue
            old_number = int(match.group(1))
            new_number = assignments.get(old_number, old_number)
            if new_number == old_number or str(old_number) in staged:
                continue
            source = item["path"]
            temporary = os.path.join(
                SESSIONS_DIR,
                f"_repair_{old_number}_{plan['id']}.session",
            )
            # Persist the exact source/temporary pair before moving anything.
            # If the process stops immediately after the move, startup can
            # still find and continue the operation.
            staged[str(old_number)] = temporary
            _save_repair_state(plan)
            if os.path.exists(source):
                shutil.move(source, temporary)
                journal = source + "-journal"
                if os.path.exists(journal):
                    shutil.move(journal, temporary + "-journal")
            _save_repair_state(plan)
        plan["phase"] = "finalize"
        _save_repair_state(plan)

    if plan.get("phase") == "finalize":
        staged = plan.get("staged", {})
        for old_number, temporary in staged.items():
            destination = os.path.join(
                SESSIONS_DIR, f"Clone{assignments[int(old_number)]}.session"
            )
            if os.path.exists(temporary):
                shutil.move(temporary, destination)
                temporary_journal = temporary + "-journal"
                if os.path.exists(temporary_journal):
                    shutil.move(temporary_journal, destination + "-journal")
            _save_repair_state(plan)
        plan["phase"] = "metadata"
        _save_repair_state(plan)

    if plan.get("phase") == "metadata":
        for item in valid_active:
            match = re.search(r"(\d+)$", item["name"])
            if not match:
                continue
            old_number = int(match.group(1))
            new_number = assignments.get(old_number, old_number)
            identity = await _read_session_identity(
                os.path.join(SESSIONS_DIR, f"Clone{new_number}.session")
            )
            if identity:
                save_session_info(
                    f"Clone{new_number}", identity["phone"], "clone"
                )
            if old_number != new_number:
                delete_session_info(item["name"])
        plan["phase"] = "profiles"
        _save_repair_state(plan)

    if plan.get("phase") == "profiles":
        from clone_settings.normal_mode_store import reindex_clone_profiles
        reindex_clone_profiles(old_to_new, repair_id=plan["id"])
        plan["phase"] = "registry"
        _save_repair_state(plan)

    if plan.get("phase") == "registry":
        registry = await rebuild_phone_registry()
        state = load_state()
        state["invalid_sessions"] = [item["name"] for item in invalid]
        state["repair_ran"] = True
        save_state(state)
        plan["registry_count"] = registry["count"]
        plan["phase"] = "done"
        _save_repair_state(plan)

    _clear_repair_state()
    renamed = {
        item["name"]: f"Clone{assignments[int(re.search(r'(\d+)$', item['name']).group(1))]}"
        for item in valid_active
        if int(re.search(r"(\d+)$", item["name"]).group(1)) in assignments
    }
    return {
        "success": True,
        "removed": [item["name"] for item in invalid],
        "renamed": renamed,
        "disabled_clones": len(
            [item for item in plan.get("valid", [])
             if item["name"].lower().startswith("disabled")]
        ),
        "active_clones": len(valid_active),
        "registered_phones": plan.get("registry_count", 0),
    }


def _session_info_path(name: str) -> str:
    return os.path.join(SESSION_INFO_DIR, f"{name}.json")


def save_session_info(name: str, phone: str, session_type: str):
    """Persist the phone returned by Telegram for one session."""
    _ensure_dirs()
    path = _session_info_path(name)
    payload = {"phone": normalize_phone(phone)}
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.error("[SESSION] Cannot save %s: %s", path, exc)


def delete_session_info(name: str):
    try:
        path = _session_info_path(name)
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("[SESSION] Cannot delete session info %s: %s", name, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Phone number registry
# ─────────────────────────────────────────────────────────────────────────────

def normalize_phone(phone: str) -> str:
    """Normalize phone number to +XXXXXXXXX format."""
    p = re.sub(r'[^\d+]', '', phone.strip())
    if not p.startswith('+'):
        p = '+' + p
    return p


def is_phone_used(phone: str) -> Optional[Dict]:
    """Check if a phone is already registered. Returns info dict or None."""
    state = load_state()
    normalized = normalize_phone(phone)
    return state.get("used_phones", {}).get(normalized)


def register_phone(phone: str, name: str, session_type: str):
    """Register a phone as used."""
    state = load_state()
    normalized = normalize_phone(phone)
    state["used_phones"][normalized] = {
        "name": name,
        "type": session_type,
    }
    save_state(state)
    logger.info(f"[PHONE-REG] Registered {normalized} → {name} ({session_type})")


def unregister_phone(phone: str):
    """Remove a phone from registry."""
    state = load_state()
    normalized = normalize_phone(phone)
    if normalized in state.get("used_phones", {}):
        del state["used_phones"][normalized]
        save_state(state)
        logger.info(f"[PHONE-REG] Unregistered {normalized}")


def unregister_phone_by_name(name: str):
    """Remove all phones registered to a given session name."""
    state = load_state()
    phones = state.get("used_phones", {})
    to_remove = [p for p, info in phones.items() if info.get("name") == name]
    for p in to_remove:
        del phones[p]
        logger.info(f"[PHONE-REG] Unregistered {p} (was {name})")
    if to_remove:
        save_state(state)


def get_used_phones() -> Dict[str, Dict]:
    """Get all registered phones."""
    state = load_state()
    return state.get("used_phones", {})


# ─────────────────────────────────────────────────────────────────────────────
# List sessions
# ─────────────────────────────────────────────────────────────────────────────

def list_all_mains() -> List[Dict]:
    """List all Main sessions (active + pending)."""
    _ensure_dirs()
    try:
        from commander_manager import active_name, list_commanders, path_for
        active = active_name()
        return [{
                "name": name,
                "active": name == active,
                "path": str(path_for(name)),
            } for name in list_commanders()]
    except Exception:
        pass

    mains = []
    active = load_state().get("active_main", MAIN_SESSION_NAME)
    for f in os.listdir(PENDING_DIR):
        if f.endswith(".session") and not f.startswith("_"):
            name = f[:-len(".session")]
            if name != active:
                mains.append({
                    "name": name,
                    "active": False,
                    "path": os.path.join(PENDING_DIR, f),
                })

    return mains


def list_clones() -> List[Dict]:
    """List all clone sessions."""
    _ensure_dirs()
    state = load_state()
    active_main = state.get("active_main", MAIN_SESSION_NAME)

    clones = []
    for f in sorted(os.listdir(SESSIONS_DIR)):
        if not f.endswith(".session"):
            continue
        name = f[:-len(".session")]
        if name.startswith("_"):
            continue
        try:
            from commander_manager import is_commander_name
            if is_commander_name(name):
                continue
        except Exception:
            pass
        if name.lower() == active_main.lower():
            continue
        clones.append({
            "name": name,
            "path": os.path.join(SESSIONS_DIR, f),
        })

    def _key(item):
        parts = re.split(r'(\d+)', item["name"])
        return [int(x) if x.isdigit() else x.lower() for x in parts]

    clones.sort(key=_key)
    return clones


def list_session_slots() -> List[Dict]:
    """Return active and disabled clone slots in natural numeric order."""
    _ensure_dirs()
    slots = []
    for filename in os.listdir(SESSIONS_DIR):
        stem, suffix = os.path.splitext(filename)
        if suffix.lower() != ".session" or stem.startswith("_"):
            continue
        match = re.fullmatch(r"(clone|disabled)(\d+)", stem, re.IGNORECASE)
        if not match:
            continue
        number = int(match.group(2))
        active = match.group(1).lower() == "clone"
        slots.append({
            "number": number,
            "name": stem,
            "active": active,
            "path": os.path.join(SESSIONS_DIR, filename),
        })
    return sorted(slots, key=lambda item: (item["number"], not item["active"]))


def _rename_session_and_info(source_name: str, destination_name: str) -> None:
    """Rename a session and its sidecar metadata together."""
    source = os.path.join(SESSIONS_DIR, f"{source_name}.session")
    destination = os.path.join(SESSIONS_DIR, f"{destination_name}.session")
    if not os.path.exists(source):
        raise FileNotFoundError(source)
    if os.path.exists(destination):
        raise FileExistsError(destination)
    shutil.move(source, destination)
    source_journal = source + "-journal"
    destination_journal = destination + "-journal"
    if os.path.exists(source_journal):
        shutil.move(source_journal, destination_journal)

    source_info = _session_info_path(source_name)
    destination_info = _session_info_path(destination_name)
    if os.path.exists(source_info):
        shutil.move(source_info, destination_info)

    state = load_state()
    changed = False
    for info in state.get("used_phones", {}).values():
        if info.get("name") == source_name:
            info["name"] = destination_name
            info["type"] = "clone"
            changed = True
    if changed:
        save_state(state)


def session_repair_was_run() -> bool:
    return bool(load_state().get("repair_ran", False))


def get_next_clone_number() -> int:
    numbers = [item["number"] for item in list_session_slots()]
    return max(numbers, default=0) + 1


def rename_clone_slot(number: int, enabled: bool, target_number: Optional[int] = None) -> str:
    """Enable or disable one clone slot and keep metadata synchronized."""
    source_name = f"Clone{number}" if enabled else f"disabled{number}"
    if enabled:
        target_number = number if target_number is None else int(target_number)
        destination_name = f"Clone{target_number}"
    else:
        destination_name = f"disabled{number}"
    _rename_session_and_info(source_name, destination_name)
    if enabled and target_number != number:
        try:
            from clone_settings.normal_mode_store import reindex_clone_profiles
            reindex_clone_profiles({str(number): str(target_number)})
        except Exception as exc:
            logger.error("[SESSION] Profile sync failed: %s", exc, exc_info=True)
    return destination_name


def _session_type_for_name(name: str) -> str:
    return "clone" if re.match(r"(clone|disabled)\d+$", name, re.IGNORECASE) else "main"


async def _read_session_identity(path: str) -> Optional[Dict]:
    """Read the real phone number and authorization state from a session."""
    session_path = path[:-len(".session")] if path.endswith(".session") else path
    client = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None
        me = await client.get_me()
        phone = getattr(me, "phone", None)
        if not phone:
            return None
        return {
            "phone": normalize_phone(phone),
            "name": os.path.basename(session_path),
            "type": _session_type_for_name(os.path.basename(session_path)),
        }
    except Exception as exc:
        logger.warning(
            "[SESSION-CHECK] Failed %s: %s",
            os.path.basename(session_path),
            exc,
        )
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def rebuild_phone_registry() -> Dict:
    """Rebuild the phone registry from authorized session files."""
    _ensure_dirs()
    entries = []
    paths = []
    for directory in (SESSIONS_DIR, PENDING_DIR):
        for filename in os.listdir(directory):
            if filename.endswith(".session") and not filename.startswith("_"):
                paths.append(os.path.join(directory, filename))

    for path in sorted(paths):
        identity = await _read_session_identity(path)
        if identity:
            entries.append(identity)

    state = load_state()
    state["used_phones"] = {
        item["phone"]: {
            "name": item["name"],
            "type": item["type"],
        }
        for item in entries
    }
    save_state(state)
    return {"success": True, "count": len(entries), "entries": entries}


def _remove_session_files(path: str):
    for candidate in (path, path + "-journal"):
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except OSError as exc:
            logger.error("[REPAIR] Cannot remove %s: %s", candidate, exc)


async def repair_clone_sessions(progress_callback=None) -> Dict:
    """Repair clone sessions by filling gaps from the end of the list.

    For example, if Clone3, Clone4, and Clone5 are missing while Clone10 is
    the last valid clone, the repair moves Clone10 to Clone3, then the new
    last clone to Clone4, and then the next last clone to Clone5.  This keeps
    existing lower slots stable and synchronizes all profile metadata.
    """
    pending = _load_repair_state()
    if pending:
        logger.warning("[REPAIR] Resuming persisted repair plan")
        result = await _execute_repair_plan(pending, progress_callback)
        commander_report = await _check_commander_sessions()
        result["commanders_checked"] = commander_report["checked"]
        result["invalid_commanders"] = commander_report["invalid"]
        return result

    commander_report = await _check_commander_sessions()
    clones = [
        item for item in list_clones()
        if re.fullmatch(r"(clone|disabled)\d+", item["name"], re.IGNORECASE)
    ]
    valid = []
    invalid = []

    async def report(index, name, phase, completed, total):
        if progress_callback is None:
            return
        result = progress_callback(index, name, phase, completed, total)
        if asyncio.iscoroutine(result):
            await result

    total = len(clones)
    for index, item in enumerate(clones, start=1):
        await report(index, item["name"], "running", index - 1, total)
        identity = await _read_session_identity(item["path"])
        if identity:
            valid.append(item)
            await report(index, item["name"], "done", index, total)
        else:
            invalid.append(item)
            await report(index, item["name"], "failed", index, total)

    valid_active = [
        item for item in valid if item["name"].lower().startswith("clone")
    ]
    valid_disabled = [item for item in valid if item["name"].lower().startswith("disabled")]
    old_to_new = {}
    active_by_number = {}
    for item in valid_active:
        match = re.search(r"(\d+)$", item["name"])
        if match:
            active_by_number[int(match.group(1))] = item

    # Fill missing slots by moving the current highest-numbered clone into
    # each gap.  The source slot is removed from the active set after moving.
    assignments = {}
    if active_by_number:
        highest_number = max(active_by_number)
        missing_numbers = [
            number
            for number in range(1, highest_number + 1)
            if number not in active_by_number
        ]
        current_numbers = set(active_by_number)
        for target_number in missing_numbers:
            source_candidates = [
                number for number in current_numbers if number > target_number
            ]
            if not source_candidates:
                break
            source_number = max(source_candidates)
            assignments[source_number] = target_number
            current_numbers.remove(source_number)
            current_numbers.add(target_number)
            old_to_new[str(source_number)] = str(target_number)
            old_to_new[str(target_number)] = "0"

    # Preserve all slots that did not move.
    for number in active_by_number:
        old_to_new.setdefault(str(number), str(number))

    # Any invalid or missing slot metadata must be removed, including a
    # leftover clone5.json when Clone5.session no longer exists.
    all_original_numbers = set(active_by_number)
    all_original_numbers.update(
        int(match.group(1))
        for item in invalid
        if (match := re.search(r"(\d+)$", item["name"]))
    )
    if active_by_number:
        for number in range(1, max(active_by_number) + 1):
            if number not in all_original_numbers:
                old_to_new[str(number)] = "0"

    for item in invalid:
        match = re.search(r"(\d+)$", item["name"])
        if match:
            old_to_new[match.group(1)] = "0"

    plan = {
        "id": str(random.randint(100000, 999999)),
        "phase": "delete_invalid",
        "invalid": invalid,
        "valid": valid,
        "valid_active": valid_active,
        "assignments": {
            str(old): str(new) for old, new in assignments.items()
        },
        "old_to_new": old_to_new,
        "staged": {},
    }
    # The complete mapping is durable before the first file is changed.
    _save_repair_state(plan)
    result = await _execute_repair_plan(plan, progress_callback)
    result["commanders_checked"] = commander_report["checked"]
    result["invalid_commanders"] = commander_report["invalid"]
    return result


async def _check_commander_sessions() -> Dict:
    """Validate commander sessions without treating them as clone slots."""
    try:
        from commander_manager import ensure_migrated, list_commanders, path_for
        ensure_migrated()
        names = list_commanders()
    except Exception:
        names = []
        path_for = None
    invalid = []
    for name in names:
        # Commanders can be either active in `sessions/` or parked in
        # `sessions/_pending/` during commander switching. Always resolve the
        # real path instead of checking only the active directory.
        path = (
            str(path_for(name))
            if path_for is not None
            else os.path.join(SESSIONS_DIR, f"{name}.session")
        )
        identity = await _read_session_identity(path)
        if identity is None:
            invalid.append(name)
    return {"checked": len(names), "invalid": invalid}


# ─────────────────────────────────────────────────────────────────────────────
# Slot management (find next available number)
# ─────────────────────────────────────────────────────────────────────────────

def get_used_clone_numbers() -> List[int]:
    """Get all clone numbers currently in use."""
    numbers = []
    for f in os.listdir(SESSIONS_DIR):
        if f.startswith("Clone") and f.endswith(".session"):
            name = f[:-len(".session")]
            try:
                n = int(name[len("Clone"):])
                numbers.append(n)
            except ValueError:
                pass
    return sorted(numbers)


def get_used_main_numbers() -> List[int]:
    """Get commander slot numbers currently in use."""
    try:
        from commander_manager import list_commanders
        numbers = []
        for name in list_commanders():
            if name == "main_commander":
                numbers.append(1)
            else:
                match = re.fullmatch(r"commander(\d+)", name, re.IGNORECASE)
                if match:
                    numbers.append(int(match.group(1)) + 1)
        return sorted(numbers)
    except Exception:
        return []


def get_next_clone_name() -> str:
    """Choose the next clone name according to the repair history.

    Before the first Session Repair, a missing slot is reusable.  This lets a
    newly-created clone replace a deleted session such as Clone5.  Once a
    repair has been performed, numbering stays append-only so repaired slot
    assignments are not silently changed.
    """
    used = set(get_used_clone_numbers())
    if not session_repair_was_run():
        n = 1
        while n in used:
            n += 1
        return f"Clone{n}"

    n = max(used, default=0) + 1
    return f"Clone{n}"


def get_next_main_name() -> str:
    """Find the next available commander session name."""
    used = set(get_used_main_numbers())

    if 1 not in used:
        return MAIN_SESSION_NAME

    n = 2
    while n in used:
        n += 1
    return f"commander{n - 1}"


# ─────────────────────────────────────────────────────────────────────────────
# Active Main management
# ─────────────────────────────────────────────────────────────────────────────

def set_active_main(new_main_name: str) -> Dict:
    """Swap the active Main session."""
    _ensure_dirs()
    state = load_state()
    current_active = state.get("active_main", MAIN_SESSION_NAME)

    if new_main_name == current_active:
        return {"success": True, "message": f"'{new_main_name}' is already active"}

    new_path_pending = os.path.join(PENDING_DIR, f"{new_main_name}.session")
    new_path_active = os.path.join(SESSIONS_DIR, f"{new_main_name}.session")

    if not os.path.exists(new_path_pending) and not os.path.exists(new_path_active):
        return {"success": False, "message": f"Session '{new_main_name}' not found"}

    current_path = os.path.join(SESSIONS_DIR, f"{current_active}.session")
    if os.path.exists(current_path):
        try:
            if current_active == MAIN_SESSION_NAME:
                used = set(get_used_main_numbers())
                n = 2
                while n in used:
                    n += 1
                new_pending_name = f"Main{n}"
            else:
                new_pending_name = current_active

            dest = os.path.join(PENDING_DIR, f"{new_pending_name}.session")
            shutil.move(current_path, dest)

            journal = current_path + "-journal"
            if os.path.exists(journal):
                try:
                    shutil.move(journal, dest + "-journal")
                except Exception:
                    pass

            logger.info(f"[SESSION] {current_active} → _pending/{new_pending_name}")
        except Exception as e:
            return {"success": False, "message": f"Failed to move current: {e}"}

    if os.path.exists(new_path_pending):
        try:
            dest = os.path.join(SESSIONS_DIR, f"{MAIN_SESSION_NAME}.session")
            shutil.move(new_path_pending, dest)

            journal = new_path_pending + "-journal"
            if os.path.exists(journal):
                try:
                    shutil.move(journal, dest + "-journal")
                except Exception:
                    pass

            logger.info(f"[SESSION] {new_main_name} → active as {MAIN_SESSION_NAME}")
        except Exception as e:
            return {"success": False, "message": f"Failed to activate: {e}"}

    state["active_main"] = MAIN_SESSION_NAME
    save_state(state)

    return {
        "success": True,
        "message": f"Activated '{new_main_name}'. Click Reload Bot to apply.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Convert between Main ↔ Clone
# ─────────────────────────────────────────────────────────────────────────────

def convert_session(source_name: str, source_type: str, target_type: str) -> Dict:
    """
    Convert a session from one type to another.
      source_type: 'main' or 'clone'
      target_type: 'main' or 'clone'
    """
    _ensure_dirs()

    if source_type == target_type:
        return {"success": False, "message": "Source and target types are the same"}

    if source_type not in ("main", "clone") or target_type not in ("main", "clone"):
        return {"success": False, "message": "Invalid type"}

    state = load_state()
    active_main = state.get("active_main", MAIN_SESSION_NAME)

    source_path = None
    if source_type == "main":
        if source_name == active_main:
            source_path = os.path.join(SESSIONS_DIR, f"{source_name}.session")
        else:
            source_path = os.path.join(PENDING_DIR, f"{source_name}.session")
    else:
        source_path = os.path.join(SESSIONS_DIR, f"{source_name}.session")

    if not os.path.exists(source_path):
        return {"success": False, "message": f"Source session '{source_name}' not found"}

    if source_type == "main" and source_name == active_main:
        return {
            "success": False,
            "message": (
                f"Cannot convert active Main '{source_name}'. "
                f"First activate another Main, then convert this one."
            ),
        }

    if target_type == "clone":
        new_name = get_next_clone_name()
        dest_path = os.path.join(SESSIONS_DIR, f"{new_name}.session")
    else:
        new_name = get_next_main_name()
        if new_name == MAIN_SESSION_NAME:
            dest_path = os.path.join(SESSIONS_DIR, f"{new_name}.session")
        else:
            dest_path = os.path.join(PENDING_DIR, f"{new_name}.session")

    try:
        shutil.move(source_path, dest_path)

        source_journal = source_path + "-journal"
        if os.path.exists(source_journal):
            try:
                shutil.move(source_journal, dest_path + "-journal")
            except Exception:
                pass

        state = load_state()
        for phone, info in state.get("used_phones", {}).items():
            if info.get("name") == source_name:
                info["name"] = new_name
                info["type"] = target_type
        save_state(state)

        logger.info(
            f"[CONVERT] {source_name} ({source_type}) → {new_name} ({target_type})"
        )

        return {
            "success": True,
            "message": (
                f"Converted '{source_name}' → '{new_name}'. "
                f"Click Reload Bot to apply."
            ),
            "new_name": new_name,
        }
    except Exception as e:
        return {"success": False, "message": f"Convert failed: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Session creation (multi-step)
# ─────────────────────────────────────────────────────────────────────────────

_pending_creations: Dict[str, Dict] = {}
_pending_lock = threading.Lock()


def _cleanup_pending_files(pending: Dict):
    session_path_no_ext = pending.get("session_path_no_ext")
    if not session_path_no_ext:
        file_path = pending.get("file_path", "")
        if file_path.endswith(".session"):
            session_path_no_ext = file_path[:-len(".session")]

    if not session_path_no_ext:
        return

    session_file = session_path_no_ext + ".session"
    journal_file = session_file + "-journal"

    for path in (session_file, journal_file):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def _pop_pending_creation(session_id: str) -> Optional[Dict]:
    with _pending_lock:
        return _pending_creations.pop(session_id, None)


def _get_pending_creation(session_id: str) -> Optional[Dict]:
    with _pending_lock:
        return _pending_creations.get(session_id)


async def start_session_creation(
    session_type: str,
    phone: str,
) -> Dict:
    """Step 1: Send phone → Telegram sends OTP."""
    _ensure_dirs()

    if session_type not in ("main", "clone"):
        return {"success": False, "message": "Invalid session type"}

    normalized = normalize_phone(phone)

    # The registry is a cache. Do not reopen every active Telethon SQLite
    # session here: the main bot may already hold those databases open, which
    # causes "database is locked" and delays the OTP response.
    existing = is_phone_used(normalized)
    if existing:
        return {
            "success": False,
            "message": (
                f"Phone {normalized} is already registered as "
                f"'{existing['name']}' ({existing['type']}). "
                f"Use the existing session or a different number."
            ),
        }

    if session_type == "main":
        name = get_next_main_name()
        if name == MAIN_SESSION_NAME and not os.path.exists(
            os.path.join(SESSIONS_DIR, f"{MAIN_SESSION_NAME}.session")
        ):
            file_path = os.path.join(SESSIONS_DIR, f"{MAIN_SESSION_NAME}.session")
        else:
            file_path = os.path.join(PENDING_DIR, f"{name}.session")
        display_name = name
    else:
        name = get_next_clone_name()
        file_path = os.path.join(SESSIONS_DIR, f"{name}.session")
        display_name = name

    device = get_random_samsung_device()
    session_path_no_ext = file_path[:-len(".session")]

    client = TelegramClient(
        session_path_no_ext,
        API_ID,
        API_HASH,
        device_model=device["model"],
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION,
        lang_code=LANG_CODE,
        system_lang_code=SYSTEM_LANG_CODE,
    )

    try:
        await client.connect()

        if await client.is_user_authorized():
            await client.disconnect()
            _cleanup_pending_files({
                "session_path_no_ext": session_path_no_ext,
                "file_path": file_path,
            })
            return {"success": False, "message": "Session already authorized"}

        sent = await client.send_code_request(normalized)

    except PhoneNumberBannedError:
        try:
            await client.disconnect()
        except Exception:
            pass
        _cleanup_pending_files({
            "session_path_no_ext": session_path_no_ext,
            "file_path": file_path,
        })
        return {"success": False, "message": "Phone number is banned by Telegram"}

    except PhoneNumberInvalidError:
        try:
            await client.disconnect()
        except Exception:
            pass
        _cleanup_pending_files({
            "session_path_no_ext": session_path_no_ext,
            "file_path": file_path,
        })
        return {"success": False, "message": "Invalid phone number format"}

    except FloodWaitError as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        _cleanup_pending_files({
            "session_path_no_ext": session_path_no_ext,
            "file_path": file_path,
        })
        return {"success": False, "message": f"FloodWait: {e.seconds}s"}

    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        _cleanup_pending_files({
            "session_path_no_ext": session_path_no_ext,
            "file_path": file_path,
        })
        return {"success": False, "message": f"Error: {e}"}

    session_id = f"{session_type}_{name}_{normalized}"
    pending_info = {
        "client": client,
        "phone": normalized,
        "type": session_type,
        "name": display_name,
        "file_path": file_path,
        "session_path_no_ext": session_path_no_ext,
        "phone_code_hash": sent.phone_code_hash,
        "device": device,
        "loop": asyncio.get_running_loop(),
    }

    with _pending_lock:
        _pending_creations[session_id] = pending_info

    logger.info(
        f"[MAKE] Started creation: {session_type} '{display_name}' "
        f"phone={normalized} device={device['name']}"
    )

    return {
        "success": True,
        "message": f"Code sent to {normalized}",
        "session_id": session_id,
        "device": device["name"],
        "name": display_name,
    }


async def complete_session_creation(
    session_id: str,
    code: str,
    password: Optional[str] = None,
    setup_2fa_password: Optional[str] = None,
    setup_2fa_hint: Optional[str] = None,
) -> Dict:
    """
    Step 2: Enter OTP (+ optional 2FA login password).
    Optionally set up NEW 2FA password after successful login.
    """
    pending = _get_pending_creation(session_id)
    if pending is None:
        return {"success": False, "message": "Invalid or expired session_id"}

    client: TelegramClient = pending["client"]

    try:
        await client.sign_in(
            phone=pending["phone"],
            code=code,
            phone_code_hash=pending["phone_code_hash"],
        )
    except Exception as e:
        err_str = str(e)
        if (
            "password" in err_str.lower()
            or "2fa" in err_str.lower()
            or "SESSION_PASSWORD_NEEDED" in err_str
        ):
            if not password:
                return {
                    "success": False,
                    "needs_password": True,
                    "message": "2FA password required for this account",
                }
            try:
                await client.sign_in(password=password)
            except Exception as e2:
                return {"success": False, "message": f"2FA login failed: {e2}"}
        else:
            return {"success": False, "message": f"Sign-in failed: {e}"}

    try:
        me = await client.get_me()
        info = {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "phone": pending["phone"],
        }
    except Exception:
        info = {}

    twofa_result = None
    if setup_2fa_password:
        try:
            await client.edit_2fa(
                new_password=setup_2fa_password,
                hint=setup_2fa_hint or "",
            )
            twofa_result = "2FA password set successfully"
            logger.info(f"[MAKE] [{pending['name']}] ✓ 2FA password configured")
        except Exception as e:
            twofa_result = f"2FA setup failed: {e}"
            logger.error(f"[MAKE] [{pending['name']}] 2FA setup error: {e}")

    try:
        await client.disconnect()
    except Exception:
        pass

    server_phone = info.get("phone") or pending["phone"]
    save_session_info(pending["name"], server_phone, pending["type"])
    register_phone(server_phone, pending["name"], pending["type"])
    _pop_pending_creation(session_id)

    logger.info(
        f"[MAKE] ✓ Created {pending['type']} '{pending['name']}' "
        f"→ {info.get('first_name', '?')} (@{info.get('username', '?')})"
    )

    return {
        "success": True,
        "message": f"Session '{pending['name']}' created successfully",
        "info": info,
        "name": pending["name"],
        "type": pending["type"],
        "twofa_result": twofa_result,
    }


def cancel_session_creation(session_id: str) -> Dict:
    """Cancel pending creation."""
    pending = _pop_pending_creation(session_id)
    if pending is None:
        return {"success": False, "message": "Session ID not found"}

    client = pending["client"]
    loop = pending.get("loop")

    try:
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
    except Exception:
        pass

    _cleanup_pending_files(pending)
    logger.info(f"[MAKE] Cancelled pending creation: {pending.get('name', session_id)}")
    return {"success": True, "message": "Cancelled"}


# ─────────────────────────────────────────────────────────────────────────────
# Delete session
# ─────────────────────────────────────────────────────────────────────────────

def delete_session(name: str, session_type: str) -> Dict:
    """Delete a session and unregister its phone."""
    _ensure_dirs()

    if session_type == "main":
        paths = [
            os.path.join(SESSIONS_DIR, f"{name}.session"),
            os.path.join(PENDING_DIR, f"{name}.session"),
        ]
    else:
        paths = [os.path.join(SESSIONS_DIR, f"{name}.session")]

    deleted = False
    for path in paths:
        if os.path.exists(path):
            try:
                os.remove(path)
                journal = path + "-journal"
                if os.path.exists(journal):
                    os.remove(journal)
                deleted = True
                logger.info(f"[SESSION] Deleted: {path}")
            except Exception as e:
                return {"success": False, "message": f"Delete failed: {e}"}

    if not deleted:
        return {"success": False, "message": f"Session '{name}' not found"}

    unregister_phone_by_name(name)
    return {"success": True, "message": f"Deleted '{name}'"}


# ─────────────────────────────────────────────────────────────────────────────
# Bridge group management
# ─────────────────────────────────────────────────────────────────────────────

def set_bridge_invite_link(link: str) -> Dict:
    state = load_state()
    state["bridge_invite_link"] = link.strip()
    save_state(state)
    logger.info("[SESSION] Bridge invite link updated")
    return {"success": True, "message": "Invite link saved"}


def get_bridge_invite_link() -> str:
    # The environment configuration is the source of truth when present.
    # This also makes changes in .env effective even if the legacy state file
    # contains an empty or outdated bridge link.
    configured_link = (BRIDGE_INVITE_LINK or "").strip()
    if configured_link:
        return configured_link
    state = load_state()
    return (state.get("bridge_invite_link", "") or "").strip()


def bridge_join_was_completed(session_name: str) -> bool:
    """Return whether this session already completed the current bridge join."""
    state = load_state()
    link = get_bridge_invite_link()
    completed = state.get("bridge_joined", {})
    return bool(link and completed.get(session_name) == link)


def mark_bridge_join_completed(session_name: str):
    """Persist successful bridge membership for the configured invite link."""
    state = load_state()
    link = get_bridge_invite_link()
    if not link:
        return
    completed = state.setdefault("bridge_joined", {})
    completed[session_name] = link
    save_state(state)


async def auto_join_bridge(client: TelegramClient, session_name: str) -> bool:
    invite_link = get_bridge_invite_link()

    if not invite_link:
        logger.warning(f"[BRIDGE-JOIN] [{session_name}] No invite link configured")
        return False

    invite_hash = None
    for prefix in (
        "https://t.me/+",
        "https://t.me/joinchat/",
        "t.me/+",
        "t.me/joinchat/",
    ):
        if invite_link.startswith(prefix):
            invite_hash = invite_link[len(prefix):]
            break

    is_public = False
    public_name = None
    if invite_hash is None:
        for prefix in ("https://t.me/", "t.me/", "@"):
            if invite_link.startswith(prefix):
                public_name = invite_link[len(prefix):]
                is_public = True
                break

    try:
        if is_public and public_name:
            entity = await client.get_entity(public_name)
            await client(JoinChannelRequest(entity))
            logger.info(f"[BRIDGE-JOIN] [{session_name}] ✓ Joined public group")
        elif invite_hash:
            await client(ImportChatInviteRequest(hash=invite_hash))
            logger.info(f"[BRIDGE-JOIN] [{session_name}] ✓ Joined via invite")
        else:
            logger.error(f"[BRIDGE-JOIN] [{session_name}] Invalid invite link")
            return False
        mark_bridge_join_completed(session_name)
        return True

    except UserAlreadyParticipantError:
        logger.info(f"[BRIDGE-JOIN] [{session_name}] Already a member")
        mark_bridge_join_completed(session_name)
        return True

    except (InviteHashExpiredError, InviteHashInvalidError) as e:
        logger.error(f"[BRIDGE-JOIN] [{session_name}] Invalid invite: {e}")
        return False

    except FloodWaitError as e:
        logger.warning(f"[BRIDGE-JOIN] [{session_name}] FloodWait {e.seconds}s")
        return False

    except Exception as e:
        logger.error(
            f"[BRIDGE-JOIN] [{session_name}] Failed: {type(e).__name__}: {e}"
        )
        return False

"""Clone list menu — shows phone number and Telegram user ID."""

import os

from ..utils.keyboards import make_inline_keyboard


CLONES_PER_PAGE = 10


def _natural_name_key(name: str):
    number = name[5:]
    try:
        return int(number)
    except ValueError:
        return number.lower()


def _clone_session_names() -> list[str]:
    """Return every Clone*.session name, including offline sessions."""
    try:
        from config import SESSIONS_DIR

        names = []
        for filename in os.listdir(SESSIONS_DIR):
            stem, suffix = os.path.splitext(filename)
            if suffix.lower() == ".session" and stem.lower().startswith("clone"):
                names.append(stem)
        return sorted(names, key=_natural_name_key)
    except (FileNotFoundError, OSError):
        return []


def _session_phone(name: str) -> str:
    """Read saved phone metadata for a session."""
    try:
        import json
        from config import SESSIONS_DIR
        with open(os.path.join(SESSIONS_DIR, f"{name}.json"), encoding="utf-8") as handle:
            phone = json.load(handle).get("phone")
        return str(phone) if phone else "—"
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return "—"


def _client_info_by_name(automation) -> dict:
    """Map loaded clients by name without shifting failed clone indexes."""
    result = {}
    clients = getattr(automation, "clone_clients", [])
    for index, name in enumerate(getattr(automation, "clone_names", [])):
        if index >= len(clients):
            continue
        client = clients[index]
        phone = None
        try:
            me = getattr(client, "_self_user", None)
            if me:
                phone = getattr(me, "phone", None)
        except Exception:
            pass
        result[name] = {
            "phone": str(phone) if phone else None,
        }
    return result


def build_clone_list(automation, page: int = 1):
    """Build a plain-text, ten-items-per-page clone list."""
    names = _clone_session_names()
    total = len(names)

    if not total:
        return (
            "Clone List\n\nNo clone sessions found.",
            make_inline_keyboard([[("Back", "menu:main")]]),
        )

    total_pages = max(1, (total + CLONES_PER_PAGE - 1) // CLONES_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * CLONES_PER_PAGE
    client_info = _client_info_by_name(automation)

    lines = ["Clone List", ""]
    for name in names[start:start + CLONES_PER_PAGE]:
        info = client_info.get(name, {})
        phone = info.get("phone") or _session_phone(name)
        lines.append(f"{name} {phone}")

    navigation = [
        ("Back", f"page:clone_list:{max(1, page - 1)}"
         if page > 1 else "noop"),
        ("Main Menu", "menu:main"),
        ("Next", f"page:clone_list:{min(total_pages, page + 1)}"
         if page < total_pages else "noop"),
    ]
    rows = [navigation]
    return "\n".join(lines), make_inline_keyboard(rows)

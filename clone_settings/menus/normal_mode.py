"""Normal Mode menu for independent per-clone profiles."""

__TCM_FILE_HASH__ = "6382051749"


from ..normal_mode_store import load_active_mode, load_settings
from ..utils.keyboards import make_inline_keyboard
from ..normal_mode_store import NORMAL_MODE_DIR


def _profile(clone_idx):
    settings = load_settings()
    return settings["normal"]["clones"].get(str(clone_idx), {})


def normal_photo_path(clone_idx):
    photo_name = _profile(clone_idx).get("photo", "")
    if not photo_name:
        return None
    path = NORMAL_MODE_DIR / photo_name
    return path if path.is_file() else None


def build_normal_main_menu(automation, inline=False, delegated=False):
    return build_normal_clone_menu(
        automation,
        1,
        inline=inline,
        delegated=delegated,
    )


def build_normal_clone_menu(
    automation,
    clone_idx: int,
    inline=False,
    delegated=False,
):
    total = len(getattr(automation, "clone_clients", []))
    if total == 0:
        return (
            "Normal Mod Settings:\n\nNo clones found.",
            make_inline_keyboard(
                [] if delegated else [[("Main Menu〽️", "menu:main")]]
            ),
        )

    clone_idx = max(1, min(clone_idx, total))
    profile = _profile(clone_idx)
    photo = profile.get("photo") or "NonePic"
    first_name = profile.get("first_name") or "--"
    last_name = profile.get("last_name") or "--"
    bio = profile.get("bio") or "--"
    enabled = load_active_mode() == "normal"

    photo_line = f"Profile Photo: {'✅ Set' if photo != 'NonePic' else 'NonePic'}"
    if inline and photo != "NonePic":
        photo_line += "\n(Not displayed in inline mode)"

    text = (
        "Normal Mod Settings:\n\n"
        f"{photo_line}\n"
        f"First Name: {first_name}\n"
        f"Last Name: {last_name}\n"
        f"Bio: {bio}\n\n"
        f"Clone {clone_idx}/{total}"
    )
    previous = clone_idx - 1 if clone_idx > 1 else total
    following = clone_idx + 1 if clone_idx < total else 1
    buttons = [
        [
            ("⏮Back", f"menu:normal:{previous}"),
            (f"👤Clone{clone_idx}", "noop"),
            ("Next⏭", f"menu:normal:{following}"),
        ],
        [("ON🟢" if enabled else "OFF🟥", "action:normal:toggle")],
        [
            ("First Name✏️", f"action:normal:{clone_idx}:first_name"),
            ("Bio💬", f"action:normal:{clone_idx}:bio"),
            ("Last Name✏️", f"action:normal:{clone_idx}:last_name"),
        ],
        [("SetPic☣", f"action:normal:{clone_idx}:photo")],
        [("Template 🧩", "menu:template")],
    ]
    if not delegated:
        if enabled:
            buttons.append([
                ("Apply⚙", f"action:normal:{clone_idx}:apply"),
                ("Main Menu〽️", "menu:main"),
            ])
        else:
            buttons.append([("Main Menu〽️", "menu:main")])
    return text, make_inline_keyboard(buttons)

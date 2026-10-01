"""Normal Mode template list and editor menus."""

from ..normal_mode_store import STORE_DIR, load_settings
from ..utils.keyboards import make_inline_keyboard


def list_template_names():
    return list(load_settings()["template"]["templates"].keys())


def _profile(template_name, clone_idx):
    template = load_settings()["template"]["templates"].get(template_name, {})
    return template.get("clones", {}).get(str(clone_idx), {})


def template_photo_path(template_name, clone_idx):
    photo = _profile(template_name, clone_idx).get("photo")
    if not photo:
        return None
    path = STORE_DIR / "template" / template_name / photo
    return path if path.is_file() else None


def build_template_list_menu(delegated=False):
    names = list_template_names()
    lines = ["Normal Mode Templates:", ""]
    lines.append(
        "You do not have any templates yet."
        if not names else
        "\n".join(f"• {name}" for name in names)
    )
    rows = [
        [(name, f"menu:template:open:{name}") for name in names[i:i + 2]]
        for i in range(0, len(names), 2)
    ]
    rows.append([("Add Template ➕", "action:template:add")])
    if not delegated:
        rows.append([("💾Back&Save🔙", "menu:normal_main")])
    return "\n".join(lines), make_inline_keyboard(rows)


def build_template_menu(
    automation,
    template_name,
    clone_idx=1,
    delegated=False,
):
    total = len(getattr(automation, "clone_clients", []))
    clone_idx = max(1, min(int(clone_idx), max(total, 1)))
    profile = _profile(template_name, clone_idx)
    from ..normal_mode_store import load_active_mode
    normal_enabled = load_active_mode() == "normal"
    text = (
        f"Template: {template_name}\n\n"
        f"Profile Photo: {'✅ Set' if template_photo_path(template_name, clone_idx) else 'NonePic'}\n"
        f"First Name: {profile.get('first_name') or '--'}\n"
        f"Last Name: {profile.get('last_name') or '--'}\n"
        f"Bio: {profile.get('bio') or '--'}\n\n"
        f"Clone {clone_idx}/{total}"
    )
    previous = total if clone_idx == 1 else clone_idx - 1
    following = 1 if clone_idx == total else clone_idx + 1
    keyboard = make_inline_keyboard([
        [
            ("⏮Back", f"menu:template:edit:{template_name}:{previous}"),
            (f"Clone{clone_idx}👤", "noop"),
            ("Next⏭", f"menu:template:edit:{template_name}:{following}"),
        ],
        [
            ("First Name✏️", f"action:template:field:{template_name}:{clone_idx}:first_name"),
            ("Last Name✏️", f"action:template:field:{template_name}:{clone_idx}:last_name"),
        ],
        [("Bio💬", f"action:template:field:{template_name}:{clone_idx}:bio")],
        [("SetPic☣", f"action:template:field:{template_name}:{clone_idx}:photo")],
        (
            [("Apply Template⚙", f"action:template:apply:{template_name}")]
            if normal_enabled and not delegated else []
        ),
        (
            [("💾Back&Save🔙", "menu:template")]
            if not delegated else []
        ),
    ])
    return text, keyboard

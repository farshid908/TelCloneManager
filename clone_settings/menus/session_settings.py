"""Session Settings menu with clone slot controls."""

from ..utils.keyboards import make_inline_keyboard


def _slots():
    from session_manager import list_session_slots
    return list_session_slots()


def _format_ranges(numbers):
    numbers = sorted(set(numbers))
    if not numbers:
        return "None"
    ranges = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(str(start) if start == previous else f"{start} to {previous}")
        start = previous = number
    ranges.append(str(start) if start == previous else f"{start} to {previous}")
    return ", ".join(ranges)


def _status_counts(slots):
    from session_manager import load_state
    state = load_state()
    invalid_names = set(state.get("invalid_sessions", []))
    active = []
    disabled = []
    invalid = []
    for slot in slots:
        # An empty SQLite session can never be a valid Telethon session.
        # Detect this immediately instead of waiting for Session Repair.
        is_empty = False
        try:
            import os
            is_empty = os.path.getsize(slot["path"]) == 0
        except OSError:
            is_empty = True
        if slot["name"] in invalid_names or is_empty:
            invalid.append(slot["number"])
        elif slot["active"]:
            active.append(slot["number"])
        else:
            disabled.append(slot["number"])
    present_names = {slot["name"] for slot in slots}
    for name in invalid_names - present_names:
        digits = "".join(character for character in name if character.isdigit())
        if digits:
            invalid.append(int(digits))
    active = [number for number in active if number not in invalid]
    disabled = [number for number in disabled if number not in invalid]
    return active, disabled, invalid


def build_session_settings(automation, user_id=None, page=1):
    return build_session_settings_for_user(
        automation,
        user_id=user_id,
        page=page,
    )


def build_session_settings_for_user(automation, user_id=None, page=1):
    slots = _slots()
    active, disabled, _invalid = _status_counts(slots)

    lines = [
        "🔧Session Settings⚙:",
        "",
        f"🟢Activate Sessions = {len(active)}",
        f"🟠DeActivate Sessions = {len(disabled)}",
    ]
    rows = [
        [("Activate🟢/DeActivate🟠", "session:activate_prompt")],
        [("Activates status📊", "session:activation_status")],
        [("Add Session⚛", "session:add_session")],
        [("Repair Session🛠", "session:repair")],
    ]
    try:
        from commander_manager import active_name, is_primary
        if (
            user_id is not None
            and is_primary(user_id)
            and active_name() != "main_commander"
        ):
            rows.append([("change commander👁‍🗨", "session:commanders")])
    except Exception:
        pass
    rows.append([("Main Menu〽️", "menu:main")])
    return "\n".join(lines), make_inline_keyboard(rows)


def build_commander_menu():
    from commander_manager import active_name, display_name, list_commanders

    names = list_commanders()
    active = active_name()
    # The active commander is already selected and must not appear as a
    # switch target.
    names = [name for name in names if name != active]
    rows = []
    for index in range(0, len(names), 2):
        rows.append([
            (display_name(name), f"session:commander:set:{name}")
            for name in names[index:index + 2]
        ])
    rows.append([("Main Menu〽️", "menu:main")])
    return (
        "Chose One:\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Active: {display_name(active) if active else 'None'}",
        make_inline_keyboard(rows),
    )


def build_add_session_menu():
    text = "Choose one:"
    keyboard = make_inline_keyboard([
        [
            ("☢Make Main👤", "session:create:main"),
            ("☣Make Clone👥", "session:create:clone"),
        ],
        [("🔙Back", "menu:session_settings")],
    ])
    return text, keyboard


def build_activation_status():
    slots = _slots()
    active, disabled, _invalid = _status_counts(slots)
    lines = [
        "Activates status📊:",
        "",
        f"🟢Active: Clone{_format_ranges(active)}",
        f"🟠DeActivated: Clone{_format_ranges(disabled)}",
    ]
    rows = [
        [("🔙Back", "menu:session_settings")],
    ]
    return "\n".join(lines), make_inline_keyboard(rows)


def build_session_confirmation(number, enabled):
    action = "disable" if enabled else "enable"
    name = f"Clone{number}" if enabled else f"disabled{number}"
    text = (
        f"Do you want to {action} **{name}**?\n\n"
        "Choose Yes or No."
    )
    keyboard = make_inline_keyboard([[
        ("Yes", f"session:confirm:{number}:{'disable' if enabled else 'enable'}"),
        ("No", "session:cancel"),
    ]])
    return text, keyboard


def build_activation_choice(number, new_number):
    text = (
        f"How should disabled{number} be enabled?\n"
        "Choose its original slot or a new slot."
    )
    keyboard = make_inline_keyboard([[
        (f"Clone{new_number}", f"session:enable:{number}:{new_number}"),
        (f"Clone{number}", f"session:enable:{number}:{number}"),
    ], [("No", "session:cancel")]])
    return text, keyboard

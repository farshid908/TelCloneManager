"""aiogram inline keyboard builders used by the Clone Manager."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def make_inline_keyboard(rows):
    """Build an aiogram InlineKeyboardMarkup from ``(label, callback_data)`` rows."""
    if not rows:
        return None

    keyboard = []
    for row in rows:
        if not row:
            continue

        button_row = []
        for item in row:
            if not isinstance(item, tuple):
                continue

            if len(item) == 2:
                label, data = item
                label = _normalize_button_label(label)
                button_row.append(
                    InlineKeyboardButton(
                        text=str(label),
                        callback_data=str(data)[:64],
                    )
                )
            elif len(item) == 3:
                label, value, button_type = item
                if button_type == "url":
                    button_row.append(
                        InlineKeyboardButton(text=str(label), url=str(value))
                    )
                else:
                    label = _normalize_button_label(label)
                    button_row.append(
                        InlineKeyboardButton(
                            text=str(label),
                            callback_data=str(value)[:64],
                        )
                    )

        if button_row:
            keyboard.append(button_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


def _normalize_button_label(label):
    """Keep common navigation button labels consistent across all menus."""
    text = str(label)
    if "Main Menu" in text:
        return "Main Menu〽️"
    if "Refresh" in text:
        return "Refresh🌀"
    return text


def make_confirm_keyboard(confirm_data: str, cancel_data: str = "menu:main"):
    return make_inline_keyboard(
        [[("✅ Confirm", confirm_data), ("❌ Cancel", cancel_data)]]
    )


def make_back_keyboard(
    back_data: str = "menu:main",
    back_label: str = "🏠 Main Menu",
):
    return make_inline_keyboard([[(back_label, back_data)]])


def make_pagination_keyboard(
    current_page: int,
    total_pages: int,
    data_prefix: str,
    extra_buttons: list = None,
):
    nav_row = []
    if current_page > 1:
        nav_row.append(("◀️ Prev", f"{data_prefix}:{current_page - 1}"))
    nav_row.append((f"{current_page}/{total_pages}", "noop"))
    if current_page < total_pages:
        nav_row.append(("Next ▶️", f"{data_prefix}:{current_page + 1}"))

    rows = [nav_row]
    if extra_buttons:
        rows.extend(extra_buttons)
    return make_inline_keyboard(rows)

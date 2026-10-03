"""Clone Mode settings menu."""

__TCM_FILE_HASH__ = "1278934321"


from ..utils.keyboards import make_inline_keyboard
from ..normal_mode_store import CLONE_MODE_DIR, load_active_mode, load_settings


def build_clone_mode_menu(automation):
    settings = load_settings()
    clone = settings["clone"]
    enabled = load_active_mode() == "clone"
    watermark = bool(clone.get("watermark", False))
    photo_name = clone.get("photo", "")
    has_photo = bool(photo_name and (CLONE_MODE_DIR / photo_name).is_file())

    text = (
        "Clone Mod Settings:\n\n"
        f"Profile Photo: {'✅ Set' if has_photo else 'NonePic'}"
    )
    buttons = [
        [(("ON🟢" if enabled else "OFF🟥"), "action:clone_mode:toggle")],
        [(f"WaterMark〰 {'ON🟢' if watermark else 'OFF🟥'}",
          "action:clone_mode:watermark"),
         ("WaterMark settings⚙️", "action:clone_mode:wm_settings")],
        [("SetPicClones☣", "action:clone_mode:set_photo")],
        [
            ("Edit Fname✏️", "action:clone_mode:edit_fname"),
            ("Edit Lname✏️", "action:clone_mode:edit_lname"),
        ],
        [("Edit Bio💬", "action:clone_mode:edit_bio")],
    ]
    navigation = []
    if enabled:
        navigation.append(("Apply⚙", "action:clone_mode:apply"))
    navigation.append(("Main Menu〽️", "menu:main"))
    buttons.append(navigation)
    return text, make_inline_keyboard(buttons)


def build_watermark_settings_menu(settings=None):
    from ..normal_mode_store import load_watermark_settings

    settings = settings or load_watermark_settings()
    x = settings.get("x", 50)
    y = settings.get("y", 25)
    angle = settings.get("angle", 0)
    opacity = settings.get("opacity", 55)
    darkness = settings.get("darkness", 100)
    orientation = settings.get("orientation", "horizontal")
    background = settings.get("background", "full")
    watermark_text = settings.get("text", "{NAME}")
    text = (
        "WaterMark Settings\n\n"
        f"Text: {watermark_text}\n"
        f"Position: X{x}% | Y{y}%\n"
        f"Angle: {angle}°\n"
        f"Opacity: {opacity}%\n"
        f"Blackness: {darkness}%\n"
        f"Direction: {orientation.title()}\n"
        f"Background: {'Full width' if background == 'full' else 'Text only'}"
    )
    buttons = [
        [("Text✏️", "action:clone_mode:wm_input:text")],
        [("X Position✏️", "action:clone_mode:wm_input:x"),
         ("Y Position✏️", "action:clone_mode:wm_input:y")],
        [("Angle🔄", "action:clone_mode:wm_input:angle"),
         ("Opacity🌫", "action:clone_mode:wm_input:opacity")],
        [("Blackness⚫", "action:clone_mode:wm_input:darkness")],
        [("Direction↕️", "action:clone_mode:wm_toggle:orientation"),
         ("Background▰", "action:clone_mode:wm_toggle:background")],
        [("Reset settings🔁", "action:clone_mode:wm_reset")],
        [
            ("🔙Back&Cancel❌", "action:clone_mode:wm_cancel"),
            ("🔙Back&Save💾", "action:clone_mode:wm_save"),
        ],
    ]
    return text, make_inline_keyboard(buttons)

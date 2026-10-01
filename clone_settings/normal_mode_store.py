"""Persistent profile settings for Clone Mod and Normal Mode."""

import json
import os
import re
import shutil
import tempfile
from pathlib import Path


STORE_DIR = Path(__file__).resolve().parent / "normal_mode_data"
CLONE_MODE_DIR = STORE_DIR / "clone mode"
NORMAL_MODE_DIR = STORE_DIR / "normal mode"
PHOTOS_DIR = STORE_DIR / "photos"
SETTINGS_FILE = STORE_DIR / "settings.json"
WATERMARK_SETTINGS_FILE = STORE_DIR / "watermark_settings.json"


def _default_watermark_settings():
    return {
        "text": "{NAME}",
        "x": 50,
        "y": 25,
        "angle": 0,
        "opacity": 55,
        "darkness": 100,
        "orientation": "horizontal",
        "background": "full",
    }


def load_watermark_settings():
    defaults = _default_watermark_settings()
    try:
        with WATERMARK_SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}

    if not isinstance(data, dict) or not data:
        
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
                legacy = json.load(handle)
            data = legacy.get("clone", {}).get("watermark_settings", {})
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            data = {}

    result = dict(defaults)
    if isinstance(data, dict):
        result.update({key: data[key] for key in defaults if key in data})
    return result


def _default_settings():
    return {
        "active_mode": "clone",
        "clone_mode_enabled": True,
        "normal": {"clones": {}},
        "template": {"templates": {}},
        "clone": {
            "first_name": "",
            "last_name": "",
            "bio": "",
            "photo": "",
            "watermark": False,
            "watermark_settings": _default_watermark_settings(),
            "apply_pending": False,
            "clones": {},
            "applied_profiles": {},
        },
    }


def load_settings():
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = _default_settings()

    defaults = _default_settings()
    if not isinstance(data, dict):
        data = defaults
    data.setdefault("active_mode", "normal" if data.get("enabled") else "clone")
    data["clone_mode_enabled"] = data["active_mode"] == "clone"
    data.setdefault("normal", {"clones": {}})
    data.setdefault("template", {"templates": {}})
    data.setdefault("clone", defaults["clone"].copy())
    data["normal"].setdefault("clones", {})
    if "templates" not in data["template"]:
        old_template = data["template"].pop("clones", {})
        data["template"]["templates"] = (
            {"Default": {"clones": old_template}} if old_template else {}
        )
    data["template"].setdefault("templates", {})
    for key, value in defaults["clone"].items():
        data["clone"].setdefault(key, value)
    data["clone"]["watermark_settings"] = load_watermark_settings()

    
    old_clones = data.pop("clones", {})
    if old_clones:
        data["normal"]["clones"].update(old_clones)
    data.pop("enabled", None)
    return data


def _save_settings(data):
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    CLONE_MODE_DIR.mkdir(parents=True, exist_ok=True)
    NORMAL_MODE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="settings.",
        suffix=".tmp",
        dir=str(STORE_DIR),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, SETTINGS_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_active_mode():
    mode = load_settings().get("active_mode", "clone")
    return mode if mode in {"clone", "normal"} else "clone"


def save_active_mode(mode: str):
    if mode not in {"clone", "normal"}:
        raise ValueError("mode must be clone or normal")
    data = load_settings()
    data["active_mode"] = mode
    data["clone_mode_enabled"] = mode == "clone"
    _save_settings(data)


def load_normal_mode_enabled():
    return load_active_mode() == "normal"


def save_normal_mode_enabled(enabled: bool):
    save_active_mode("normal" if enabled else "clone")


def save_clone_field(clone_idx: int, field: str, value: str):
    data = load_settings()
    clone_data = data["normal"]["clones"].setdefault(str(clone_idx), {})
    clone_data[field] = value
    _save_settings(data)
    _save_clone_json(clone_idx, clone_data)


def list_templates():
    return list(load_settings()["template"]["templates"].keys())


def create_template(name: str):
    name = str(name).strip()
    if not name:
        return False
    data = load_settings()
    templates = data["template"]["templates"]
    if name in templates:
        return False
    templates[name] = {"clones": {}}
    _save_settings(data)
    return True


def save_template_field(
    clone_idx: int,
    field: str,
    value: str,
    template_name: str = "Default",
):
    data = load_settings()
    template = data["template"]["templates"].setdefault(
        template_name, {"clones": {}}
    )
    clone_data = template.setdefault("clones", {}).setdefault(
        str(clone_idx), {}
    )
    clone_data[field] = value
    _save_settings(data)
    path = STORE_DIR / f"template_{template_name}_{clone_idx}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(clone_data, handle, ensure_ascii=False, indent=2)


def _save_clone_json(clone_idx, data):
    NORMAL_MODE_DIR.mkdir(parents=True, exist_ok=True)
    path = NORMAL_MODE_DIR / f"clone{clone_idx}.json"
    fd, temp_name = tempfile.mkstemp(
        prefix=f"clone{clone_idx}.", suffix=".tmp", dir=str(NORMAL_MODE_DIR)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def save_clone_mode_field(field: str, value: str):
    data = load_settings()
    data["clone"][field] = value
    if field != "apply_pending":
        data["clone"]["apply_pending"] = True
    _save_settings(data)


def save_watermark_setting(field: str, value):
    settings = load_watermark_settings()
    settings[field] = value
    save_watermark_settings(settings)


def save_watermark_settings(settings: dict):
    data = load_settings()
    data["clone"]["apply_pending"] = True
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="watermark_settings.", suffix=".tmp", dir=str(STORE_DIR)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
        os.replace(temp_name, WATERMARK_SETTINGS_FILE)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    _save_settings(data)


def save_clone_mode_clone_field(clone_idx: int, field: str, value: str):
    data = load_settings()
    clone_data = data["clone"].setdefault("clones", {}).setdefault(
        str(clone_idx), {}
    )
    clone_data[field] = value
    _save_settings(data)


def set_clone_mode_apply_pending(pending: bool):
    data = load_settings()
    data["clone"]["apply_pending"] = bool(pending)
    _save_settings(data)


def save_normal_music(clone_idx: int, source_path: str):
    source = Path(source_path)
    if not source.is_file():
        return None
    NORMAL_MODE_DIR.mkdir(parents=True, exist_ok=True)
    destination = NORMAL_MODE_DIR / f"clone{clone_idx}{source.suffix.lower() or '.mp3'}"
    shutil.copy2(source, destination)
    for old_file in NORMAL_MODE_DIR.iterdir():
        if (
            old_file.is_file()
            and old_file.stem == f"clone{clone_idx}"
            and old_file != destination
        ):
            old_file.unlink()
    data = load_settings()
    clone_data = data["normal"]["clones"].setdefault(str(clone_idx), {})
    clone_data["music"] = destination.name
    _save_clone_json(clone_idx, clone_data)
    _save_settings(data)
    return str(destination)


def clear_clone_mode_overrides(field=None):
    data = load_settings()
    overrides = data["clone"].setdefault("clones", {})
    if field is None:
        overrides.clear()
    else:
        for profile in overrides.values():
            profile.pop(field, None)
    _save_settings(data)


def reindex_clone_profiles(old_to_new, repair_id=None):
    """Move persisted clone-specific profiles after session renumbering."""
    data = load_settings()
    if repair_id and data.get("_last_repair_id") == str(repair_id):
        return
    normal_profiles = data["normal"].setdefault("clones", {})
    staged = []
    for path in NORMAL_MODE_DIR.glob("clone*.*"):
        match = re.match(r"clone(\d+)(\..+)$", path.name, re.IGNORECASE)
        if not match:
            continue
        new_number = old_to_new.get(match.group(1))
        if new_number and new_number != match.group(1):
            temporary = path.with_name(
                f".repair-{match.group(1)}-{path.name}"
            )
            path.rename(temporary)
            staged.append((temporary, path.with_name(
                f"clone{new_number}{match.group(2)}"
            )))
    for temporary, destination in staged:
        if destination.exists():
            
            
            original = temporary.with_name(
                f"clone{re.search(r'-(\d+)-', temporary.name).group(1)}"
                f"{temporary.suffix}"
            )
            temporary.rename(original)
            continue
        temporary.rename(destination)

    def remap_profiles(profiles):
        result = {}
        for index, profile in profiles.items():
            old = str(index)
            new = str(old_to_new.get(old, old))
            if new == "0":
                
                
                new = old
            if new in result and new != old:
                
                
                result[old] = profile
            else:
                result[new] = profile
        return result

    clone_profiles = data["clone"].setdefault("clones", {})
    data["clone"]["clones"] = remap_profiles(clone_profiles)
    data["normal"]["clones"] = remap_profiles(normal_profiles)

    if repair_id:
        data["_last_repair_id"] = str(repair_id)
    _save_settings(data)


def save_mode_photo(mode: str, source_path: str, clone_idx=None):
    """Replace a stored mode image/video and return its local path."""
    source = Path(source_path)
    if not source.is_file():
        return None
    if mode not in {"clone", "normal"}:
        raise ValueError("mode must be clone or normal")

    directory = CLONE_MODE_DIR if mode == "clone" else NORMAL_MODE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stem = "clone" if mode == "clone" else f"clone{clone_idx}"

    
    video_suffixes = {".mp4", ".mov", ".webm", ".m4v"}
    if source.suffix.lower() in video_suffixes:
        destination = directory / f"{stem}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        for old_file in directory.iterdir():
            if (
                old_file.is_file()
                and old_file.stem == stem
                and old_file != destination
            ):
                old_file.unlink()
        data = load_settings()
        if mode == "clone":
            data["clone"]["photo"] = destination.name
            data["clone"]["apply_pending"] = True
        else:
            clone_data = data["normal"]["clones"].setdefault(
                str(clone_idx), {}
            )
            clone_data["photo"] = destination.name
            _save_clone_json(clone_idx, clone_data)
        _save_settings(data)
        return str(destination)

    destination = directory / f"{stem}.png"

    
    
    try:
        from PIL import Image

        with Image.open(source) as image:
            image = image.convert("RGB")
            width, height = image.size
            side = min(width, height)
            left = (width - side) // 2
            top = (height - side) // 2
            image = image.crop((left, top, left + side, top + side))
            image.save(destination, format="PNG")
    except Exception:
        return None

    for old_file in directory.iterdir():
        if (
            old_file.is_file()
            and old_file.stem == stem
            and old_file != destination
        ):
            old_file.unlink()

    data = load_settings()
    if mode == "clone":
        data["clone"]["photo"] = destination.name
        data["clone"]["apply_pending"] = True
        save_data = data["clone"]
    else:
        data["normal"]["clones"].setdefault(str(clone_idx), {})[
            "photo"
        ] = destination.name
        save_data = data["normal"]["clones"][str(clone_idx)]
    if mode == "normal":
        _save_clone_json(clone_idx, save_data)
    _save_settings(data)
    return str(destination)


def save_normal_photo(clone_idx: int, source_path: str):
    return save_mode_photo("normal", source_path, clone_idx)


def save_template_photo(
    clone_idx: int,
    source_path: str,
    template_name: str = "Default",
):
    source = Path(source_path)
    if not source.is_file():
        return None
    directory = STORE_DIR / "template" / template_name
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"clone{clone_idx}"
    if source.suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}:
        destination = directory / f"{stem}{source.suffix.lower()}"
        shutil.copy2(source, destination)
    else:
        destination = directory / f"{stem}.png"
        try:
            from PIL import Image
            with Image.open(source) as image:
                image = image.convert("RGB")
                side = min(image.size)
                left = (image.width - side) // 2
                top = (image.height - side) // 2
                image.crop((left, top, left + side, top + side)).save(
                    destination, format="PNG"
                )
        except Exception:
            return None
    for old_file in directory.iterdir():
        if old_file.is_file() and old_file.stem == stem and old_file != destination:
            old_file.unlink()
    data = load_settings()
    template = data["template"]["templates"].setdefault(
        template_name, {"clones": {}}
    )
    clone_data = template.setdefault("clones", {}).setdefault(
        str(clone_idx), {}
    )
    clone_data["photo"] = destination.name
    _save_settings(data)
    path = STORE_DIR / f"template_{template_name}_{clone_idx}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(clone_data, handle, ensure_ascii=False, indent=2)
    return str(destination)

"""Apply persisted Clone Mod and Normal Mode profiles."""

__TCM_FILE_HASH__ = "5162849073"


import os
import re
import logging
import hashlib
import json

from telethon.tl.functions.account import UpdateProfileRequest

from profile_manager import set_profile_media
from watermark_engine import watermark_file

from .normal_mode_store import (
    CLONE_MODE_DIR,
    NORMAL_MODE_DIR,
    load_watermark_settings,
    load_settings,
    save_active_mode,
    _save_settings,
)

logger = logging.getLogger("CloneManager")
APPLY_STATE_FILE = os.path.join("sessions", "_profile_apply_state.json")


def _load_apply_state():
    try:
        with open(APPLY_STATE_FILE, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}


def _save_apply_state(state):
    os.makedirs(os.path.dirname(APPLY_STATE_FILE), exist_ok=True)
    temporary = APPLY_STATE_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, APPLY_STATE_FILE)


def _clear_apply_state():
    try:
        os.remove(APPLY_STATE_FILE)
    except FileNotFoundError:
        pass


def _clone_number(name, fallback):
    match = re.search(r"(\d+)$", name or "")
    return match.group(1) if match else str(fallback)


def _watermark_text(settings, name, index=1):
    template = settings.get("text", "{NAME}") or "{NAME}"
    number = _clone_number(name, index)
    return (
        str(template)
        .replace("{N}", number)
        .replace("{n}", number)
        .replace("{NAME}", name)
        .replace("{name}", name)
    )


async def _apply_profile(
    client,
    name,
    profile,
    mode="clone",
    watermark=False,
    watermark_settings=None,
):
    if not client.is_connected():
        return False

    
    
    
    
    current = None
    try:
        current = await client.get_me()
    except Exception:
        logger.debug("[CLONE-MODE] Could not read current profile for %s", name,
                     exc_info=True)

    profile_args = {}
    if current is not None:
        current_values = {
            "first_name": getattr(current, "first_name", "") or "",
            "last_name": getattr(current, "last_name", "") or "",
            "about": getattr(current, "about", "") or "",
        }
        for key, request_key in (
            ("first_name", "first_name"),
            ("last_name", "last_name"),
            ("bio", "about"),
        ):
            value = profile.get(key)
            if key == "first_name":
                should_update = bool(value)
            else:
                should_update = key in profile
            if should_update and str(value or "") != str(current_values[request_key]):
                profile_args[request_key] = value
    else:
        
        
        for key, request_key in (
            ("first_name", "first_name"),
            ("last_name", "last_name"),
            ("bio", "about"),
        ):
            if key == "first_name" and profile.get(key):
                profile_args[request_key] = profile[key]
            elif key in profile and key != "first_name":
                profile_args[request_key] = profile.get(key) or ""
    if profile_args:
        logger.info(
            "[CLONE-MODE] Applying profile to %s: %s",
            name,
            ", ".join(profile_args),
        )
        await client(UpdateProfileRequest(**profile_args))
    photo_name = profile.get("photo", "")
    if not photo_name:
        return True

    base_dir = CLONE_MODE_DIR if mode == "clone" else NORMAL_MODE_DIR
    photo_path = base_dir / photo_name
    if not photo_path.is_file():
        from .normal_mode_store import PHOTOS_DIR
        photo_path = PHOTOS_DIR / photo_name
    if not photo_path.is_file():
        return True
    upload_path = str(photo_path)
    watermark_path = None
    try:
        if watermark:
            watermark_path = watermark_file(
                str(photo_path),
                _watermark_text(watermark_settings or {}, name),
                name,
                settings=watermark_settings,
            )
            if watermark_path:
                upload_path = watermark_path
        return await set_profile_media(client, upload_path, name)
    finally:
        if watermark_path and os.path.exists(watermark_path):
            os.unlink(watermark_path)


def _clone_profile(settings, name, index):
    """Build the effective profile for one clone."""
    profile = dict(settings["clone"])
    clone_number = _clone_number(name, index)
    overrides = settings["clone"].get("clones", {}).get(clone_number, {})
    
    if not profile.get("first_name"):
        profile.update(overrides)
    for field in ("first_name", "last_name", "bio"):
        if profile.get(field):
            profile[field] = (
                str(profile[field])
                .replace("{N}", clone_number)
                .replace("{n}", clone_number)
            )
    return profile, bool(profile.get("watermark", False))


def _clone_profile_signature(profile, watermark):
    relevant = {
        "first_name": profile.get("first_name", ""),
        "last_name": profile.get("last_name", ""),
        "bio": profile.get("bio", ""),
        "photo": profile.get("photo", ""),
        "watermark": bool(watermark),
        "watermark_settings": (
            load_watermark_settings() if watermark else {}
        ),
    }
    payload = json.dumps(
        relevant,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepare_clone_watermarked_photos(automation, source_name):
    """Prepare a separate cached watermark image for every clone."""
    if not source_name:
        return {}
    source_path = CLONE_MODE_DIR / source_name
    if not source_path.is_file():
        return {}
    try:
        from watermark_engine import add_image_watermark

        cache_dir = CLONE_MODE_DIR / "watermarked"
        cache_dir.mkdir(parents=True, exist_ok=True)
        prepared = {}
        for index, name in enumerate(automation.clone_names, start=1):
            clone_number = _clone_number(name, index)
            settings = load_watermark_settings()
            settings_hash = hashlib.sha256(
                json.dumps(
                    settings, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()[:12]
            output_name = f"clone{clone_number}_{settings_hash}.jpg"
            output_path = cache_dir / output_name
            if (
                output_path.is_file()
                and output_path.stat().st_mtime >= source_path.stat().st_mtime
            ):
                prepared[name] = f"watermarked/{output_name}"
                continue
            if add_image_watermark(
                str(source_path),
                str(output_path),
                _watermark_text(settings, name, index),
                name,
                settings=settings,
            ):
                prepared[name] = f"watermarked/{output_name}"
        return prepared
    except Exception:
        logger.error(
            "[CLONE-MODE] Watermark cache preparation failed",
            exc_info=True,
        )
        return {}


def _save_applied_signature(name, signature, mode="clone"):
    data = load_settings()
    section = data.setdefault(mode, {})
    section.setdefault("applied_profiles", {})[name] = signature
    _save_settings(data)


async def apply_saved_mode(
    automation,
    mode=None,
    force=False,
    clone_idx=None,
    clone_indices=None,
    progress_callback=None,
):
    settings = load_settings()
    mode = mode or settings.get("active_mode", "clone")
    results = {"success": 0, "failed": 0, "skipped": 0}
    selected = [
        (index, client, name)
        for index, (client, name) in enumerate(
        zip(automation.clone_clients, automation.clone_names),
        start=1,
        )
        if (
            (clone_indices is None and (clone_idx is None or index == int(clone_idx)))
            or (clone_indices is not None and index in clone_indices)
        )
    ]
    total = len(selected)
    completed = 0
    previous = _load_apply_state()
    resume_names = set()
    if previous.get("mode") == mode and not force:
        resume_names = set(previous.get("completed", []))
    if force:
        _save_apply_state({
            "mode": mode,
            "clone_names": [name for _, _, name in selected],
            "completed": [],
        })
        
        
        
        data = load_settings()
        section = data.setdefault(mode, {})
        applied = section.get("applied_profiles")
        if isinstance(applied, dict):
            for _, _, name in selected:
                applied.pop(name, None)
            _save_settings(data)
        settings = load_settings()
    elif previous and previous.get("mode") == mode:
        logger.info(
            "[PROFILE-MODE] Resuming interrupted %s apply: %s completed",
            mode,
            len(resume_names),
        )

    for index, client, name in selected:
        if clone_indices is None and clone_idx is not None and index != int(clone_idx):
            continue
        if name in resume_names:
            results["skipped"] += 1
            completed += 1
            if progress_callback:
                await progress_callback(index, name, "skipped", completed, total)
            continue
        if mode == "normal":
            profile = settings["normal"]["clones"].get(str(index), {})
            use_watermark = False
            signature = _clone_profile_signature(profile, use_watermark)
            if (
                not force
                and settings["normal"].get("applied_profiles", {}).get(name)
                == signature
            ):
                results["skipped"] += 1
                completed += 1
                if progress_callback:
                    await progress_callback(
                        index, name, "skipped", completed, total
                    )
                continue
        else:
            profile, use_watermark = _clone_profile(settings, name, index)
            signature = _clone_profile_signature(profile, use_watermark)
            if (
                not force
                and settings["clone"].get("applied_profiles", {}).get(name)
                == signature
            ):
                results["skipped"] += 1
                completed += 1
                if progress_callback:
                    await progress_callback(
                        index, name, "skipped", completed, total
                    )
                continue
        try:
            if progress_callback:
                await progress_callback(
                    index, name, "running", completed, total
                )
            if not client.is_connected():
                results["skipped"] += 1
                completed += 1
                if progress_callback:
                    await progress_callback(
                        index, name, "skipped", completed, total
                    )
                continue
            if mode == "normal" and not any(
                profile.get(key)
                for key in ("first_name", "last_name", "bio", "photo")
            ):
                results["skipped"] += 1
                completed += 1
                if progress_callback:
                    await progress_callback(
                        index, name, "skipped", completed, total
                    )
                continue
            await _apply_profile(
                client,
                name,
                profile,
                mode=mode,
                watermark=use_watermark,
                watermark_settings=load_watermark_settings(),
            )
            _save_applied_signature(name, signature, mode=mode)
            state = _load_apply_state()
            state["mode"] = mode
            state["clone_names"] = [item[2] for item in selected]
            state.setdefault("completed", [])
            if name not in state["completed"]:
                state["completed"].append(name)
            _save_apply_state(state)
            results["success"] += 1
            completed += 1
            if progress_callback:
                await progress_callback(
                    index, name, "done", completed, total
                )
        except Exception as exc:
            results["failed"] += 1
            completed += 1
            if progress_callback:
                await progress_callback(
                    index, name, "failed", completed, total
                )
            import logging
            logging.getLogger("CloneManager").error(
                "[CLONE-MODE] Apply failed for %s: %s",
                name,
                exc,
                exc_info=True,
            )
        try:
            client._self_user = await client.get_me()
        except Exception:
            pass
    _clear_apply_state()
    return results


async def switch_mode(automation, mode, progress_callback=None):
    if mode not in {"clone", "normal"}:
        raise ValueError("mode must be clone or normal")
    save_active_mode(mode)
    automation.normal_mode = mode == "normal"
    return await apply_saved_mode(
        automation,
        mode,
        force=True,
        progress_callback=progress_callback,
    )

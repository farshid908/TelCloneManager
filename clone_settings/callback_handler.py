"""Callback and text-input handlers for the aiogram Clone Manager bot."""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.types import FSInputFile, InputMediaPhoto

from .bot_core import (
    can_access_normal_menu,
    can_operate,
    get_automation,
    get_bot_client,
    is_admin,
)
from .utils.keyboards import make_inline_keyboard

logger = logging.getLogger("CloneManager")


def _delete_message_later(message, seconds=5):
    async def _delete():
        await asyncio.sleep(seconds)
        try:
            await message.delete()
        except Exception:
            logger.debug("[CALLBACK] Timed message deletion failed", exc_info=True)
    asyncio.create_task(_delete())


async def _safe_edit_event(event, text, keyboard):
    """Edit a callback menu without surfacing Telegram's no-op error."""
    try:
        await event.edit(text, buttons=keyboard)
    except Exception as exc:
        if "message is not modified" not in str(exc).lower():
            raise
        logger.debug("[CALLBACK] Menu already up to date")


def _progress_bar(percent, width=20):
    percent = max(0, min(100, int(percent)))
    filled = round(width * percent / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}] {percent}%"


def _profile_apply_keyboard(mode, clone_idx, total):
    """Build the explicit target-selection keyboard for profile Apply."""
    from .utils.keyboards import make_inline_keyboard

    clone_idx = max(1, min(int(clone_idx or 1), max(1, total)))
    return make_inline_keyboard([
        [("Apply to all👥", f"profile_apply:all:{mode}:{clone_idx}")],
        [(f"Apply to clone{clone_idx}👤", f"profile_apply:one:{mode}:{clone_idx}")],
        [("🔙", f"profile_apply:back:{mode}:{clone_idx}")],
    ])


def _parse_clone_selection(raw, total):
    """Parse `1-5 10 17` into sorted, validated clone indexes."""
    selected = set()
    for token in (raw or "").replace(",", " ").split():
        if "-" in token:
            parts = token.split("-", 1)
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError("Use numbers and ranges such as 1-5 10 17.")
            start, end = map(int, parts)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        elif token.isdigit():
            selected.add(int(token))
        else:
            raise ValueError("Use numbers and ranges such as 1-5 10 17.")
    if not selected or any(index < 1 or index > total for index in selected):
        raise ValueError(f"Clone numbers must be between 1 and {total}.")
    return sorted(selected)


async def _update_progress_message(status, index, name, phase, completed, total):
    """Update one in-place Apply message as each clone is processed."""
    if total <= 0:
        percent = 100
    else:
        percent = int(completed * 100 / total)
    if phase == "running":
        marker = "🔄"
    elif phase == "failed":
        marker = "❌"
    elif phase == "skipped":
        marker = "⏭️"
    else:
        marker = "✅"
    try:
        await status.edit(
            f"In progress\n"
            f"Clone{index}...{marker}\n"
            f"{_progress_bar(percent)}"
        )
    except Exception:
        logger.debug("[PROGRESS] Could not update progress message", exc_info=True)
    if phase == "done":
        await asyncio.sleep(0.15)


async def _apply_with_progress(status, automation, mode, **kwargs):
    from .profile_modes import apply_saved_mode

    async def progress(index, name, phase, completed, total):
        await _update_progress_message(
            status, index, name, phase, completed, total
        )

    return await apply_saved_mode(
        automation,
        mode,
        progress_callback=progress,
        **kwargs,
    )


async def _show_text_menu(event, text, keyboard):
    """Show a text menu even when the current PV message contains a photo."""
    query = getattr(event, "_query", None)
    message = getattr(query, "message", None) if query else None
    if message is not None and message.photo:
        try:
            replacement = await message.answer(
                text,
                reply_markup=keyboard,
            )
            if replacement is not None:
                await message.delete()
            return
        except Exception:
            logger.debug("[CALLBACK] Could not replace photo menu", exc_info=True)
    await _safe_edit_event(event, text, keyboard)


async def _show_session_settings(event, automation, page=1):
    from .menus.session_settings import build_session_settings
    text, keyboard = build_session_settings(
        automation,
        user_id=event.sender_id,
        page=page,
    )
    await _show_text_menu(event, text, keyboard)
    set_pending_input(event.sender_id, "session:toggle_number", {"page": page})


async def _run_session_repair(event, automation):
    async def progress(index, name, phase, completed, total):
        percent = int(completed * 100 / total) if total else 100
        marker = "🔄" if phase == "running" else (
            "❌" if phase == "failed" else "✅"
        )
        try:
            await event.edit(
                f"In progress\n{ name }...{marker}\n"
                f"{_progress_bar(percent)}"
            )
        except Exception:
            logger.debug("[SESSION-REPAIR] Progress edit failed", exc_info=True)

    repair_method = getattr(
        automation, "repair_clone_sessions_from_manager", None
    )
    if repair_method is not None:
        result = await repair_method(progress_callback=progress)
    else:
        from session_manager import repair_clone_sessions
        result = await repair_clone_sessions(progress_callback=progress)
        if hasattr(automation, "request_reload"):
            automation.request_reload()
    await _show_session_settings(event, automation, 1)
    return result


_pending_input = {}
_callback_target = None
_watermark_drafts = {}
_profile_apply_active = False


def _profile_apply_is_active():
    if _profile_apply_active:
        return True
    try:
        from .profile_modes import _load_apply_state
        return bool(_load_apply_state())
    except Exception:
        return False


def _watermark_draft(user_id):
    from .normal_mode_store import load_watermark_settings
    return _watermark_drafts.setdefault(user_id, load_watermark_settings().copy())


def _watermark_menu_data(user_id):
    return _watermark_drafts.get(user_id) or _watermark_draft(user_id)


def get_pending_input(user_id: int) -> dict:
    return _pending_input.get(user_id)


def set_pending_input(user_id: int, input_type: str, data: dict = None):
    global _callback_target
    _pending_input[user_id] = {
        "type": input_type,
        "data": data or {},
    }
    if _callback_target is not None:
        _pending_input[user_id]["target"] = dict(_callback_target)


def clear_pending_input(user_id: int):
    _pending_input.pop(user_id, None)


def get_pending_for_users(user_ids):
    for user_id in user_ids:
        if user_id is None:
            continue
        pending = _pending_input.get(user_id)
        if pending is not None:
            return user_id, pending
    return None, None


def _is_main_user(user_id: int) -> bool:
    automation = get_automation()
    client = getattr(automation, "main_client", None) if automation else None
    main_id = (
        getattr(getattr(client, "_self_user", None), "id", None)
        or getattr(client, "_self_id", None)
    )
    return main_id is not None and user_id == main_id


def _message_kwargs(kwargs):
    translated = dict(kwargs)
    if "buttons" in translated:
        translated["reply_markup"] = translated.pop("buttons")
    return translated


def _otp_keyboard(digits=""):
    from .utils.keyboards import make_inline_keyboard
    rows = [
        [("1", "session:otp:1"), ("2", "session:otp:2"), ("3", "session:otp:3")],
        [("4", "session:otp:4"), ("5", "session:otp:5"), ("6", "session:otp:6")],
        [("7", "session:otp:7"), ("8", "session:otp:8"), ("9", "session:otp:9")],
        [("0", "session:otp:0"), ("⌫ Back", "session:otp:back")],
        [("✅ Submit", "session:otp:submit"), ("❌ Cancel", "session:create:cancel")],
    ]
    return make_inline_keyboard(rows)


def _session_cancel_confirmation_keyboard():
    return make_inline_keyboard([[
        ("Yes", "session:create:cancel:yes"),
        ("No", "session:create:cancel:no"),
    ]])


async def _edit_query_message(query, text, keyboard=None):
    if query.message is not None:
        return await query.message.edit_text(text, reply_markup=keyboard)
    if query.inline_message_id:
        return await query.bot.edit_message_text(
            inline_message_id=query.inline_message_id,
            text=text,
            reply_markup=keyboard,
        )
    return None


async def _animate_session_code(bot, target, phone, action="Sending code"):
    """Show a small live loading indicator during session authentication."""
    step = 0
    while True:
        dots = "░" * (step % 4)
        text = (
            f"📱 Phone: {phone}\n"
            f"{action}{'.' * (step % 4)}🔄\n"
            f"[{dots}]"
        )
        try:
            if target.get("inline_message_id"):
                await bot.edit_message_text(
                    inline_message_id=target["inline_message_id"],
                    text=text,
                )
            elif target.get("chat_id") and target.get("message_id"):
                await bot.edit_message_text(
                    chat_id=target["chat_id"],
                    message_id=target["message_id"],
                    text=text,
                )
        except Exception:
            logger.debug(
                "[SESSION] Could not update code loading status",
                exc_info=True,
            )
        step += 1
        await asyncio.sleep(0.8)


async def _edit_normal_menu(event, automation, clone_idx, inline=False):
    """Render Normal Mode as a photo message when a saved photo exists."""
    from .menus.normal_mode import build_normal_clone_menu, normal_photo_path

    text, keyboard = build_normal_clone_menu(
        automation, clone_idx, inline=inline
    )
    photo_path = normal_photo_path(clone_idx)
    query = getattr(event, "_query", None)
    message = getattr(query, "message", None) if query else None

    if photo_path and message is not None:
        if message.photo:
            try:
                await message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(str(photo_path)),
                        caption=text,
                    ),
                    reply_markup=keyboard,
                )
                return
            except Exception:
                logger.debug("[NORMAL] Could not edit existing photo", exc_info=True)
        else:
            try:
                replacement = await message.answer_photo(
                    photo=FSInputFile(str(photo_path)),
                    caption=text,
                    reply_markup=keyboard,
                )
                if replacement is not None:
                    await message.delete()
                return
            except Exception:
                logger.debug("[NORMAL] Could not send photo menu", exc_info=True)

    if not photo_path and message is not None and message.photo:
        try:
            
            
            
            replacement = await message.answer(
                text,
                reply_markup=keyboard,
            )
            if replacement is not None:
                await message.delete()
            return
        except Exception:
            logger.debug("[NORMAL] Could not replace photo with text", exc_info=True)

    if photo_path and query and query.inline_message_id:
        
        
        logger.debug("[NORMAL] Keeping inline Normal menu as text")

    await event.edit(text, buttons=keyboard)


async def _edit_template_menu(event, automation, template_name, clone_idx):
    from .menus.template import build_template_menu, template_photo_path

    text, keyboard = build_template_menu(automation, template_name, clone_idx)
    photo_path = template_photo_path(template_name, clone_idx)
    query = getattr(event, "_query", None)
    message = getattr(query, "message", None) if query else None
    if photo_path and message is not None:
        if message.photo:
            try:
                await message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(str(photo_path)),
                        caption=text,
                    ),
                    reply_markup=keyboard,
                )
                return
            except Exception:
                logger.debug("[TEMPLATE] Could not edit photo menu", exc_info=True)
        else:
            try:
                replacement = await message.answer_photo(
                    photo=FSInputFile(str(photo_path)),
                    caption=text,
                    reply_markup=keyboard,
                )
                if replacement:
                    await message.delete()
                return
            except Exception:
                logger.debug("[TEMPLATE] Could not send photo menu", exc_info=True)
    if not photo_path and message is not None and message.photo:
        try:
            replacement = await message.answer(text, reply_markup=keyboard)
            if replacement:
                await message.delete()
            return
        except Exception:
            logger.debug("[TEMPLATE] Could not replace photo menu", exc_info=True)
    await event.edit(text, buttons=keyboard)


async def _edit_clone_mode_menu(event, automation):
    """Render Clone Mode with its raw, non-watermarked source photo in PV."""
    from .menus.clone_mode import build_clone_mode_menu
    from .normal_mode_store import CLONE_MODE_DIR, load_settings

    text, keyboard = build_clone_mode_menu(automation)
    photo_name = load_settings()["clone"].get("photo", "")
    photo_path = CLONE_MODE_DIR / photo_name if photo_name else None
    query = getattr(event, "_query", None)
    message = getattr(query, "message", None) if query else None

    if photo_path and photo_path.is_file() and message is not None:
        if message.photo:
            try:
                await message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(str(photo_path)),
                        caption=text,
                    ),
                    reply_markup=keyboard,
                )
                return
            except Exception:
                logger.debug("[CLONE] Could not edit photo menu", exc_info=True)
        else:
            try:
                replacement = await message.answer_photo(
                    photo=FSInputFile(str(photo_path)),
                    caption=text,
                    reply_markup=keyboard,
                )
                if replacement is not None:
                    await message.delete()
                return
            except Exception:
                logger.debug("[CLONE] Could not send photo menu", exc_info=True)

    if not photo_path or not photo_path.is_file():
        if message is not None and message.photo:
            try:
                replacement = await message.answer(
                    text,
                    reply_markup=keyboard,
                )
                if replacement is not None:
                    await message.delete()
                return
            except Exception:
                logger.debug("[CLONE] Could not replace photo menu", exc_info=True)

    await _safe_edit_event(event, text, keyboard)


async def _edit_watermark_menu(event, user_id):
    from .menus.clone_mode import build_watermark_settings_menu
    from .normal_mode_store import CLONE_MODE_DIR, load_settings

    text, keyboard = build_watermark_settings_menu(_watermark_menu_data(user_id))
    query = getattr(event, "_query", None)
    message = getattr(query, "message", None) if query else None
    photo_name = load_settings()["clone"].get("photo", "")
    photo_path = CLONE_MODE_DIR / photo_name if photo_name else None

    if message is not None and photo_path and photo_path.is_file():
        if message.photo:
            try:
                await message.edit_media(
                    media=InputMediaPhoto(
                        media=FSInputFile(str(photo_path)),
                        caption=text,
                    ),
                    reply_markup=keyboard,
                )
                return
            except Exception:
                logger.debug("[WATERMARK] Could not edit photo menu", exc_info=True)
        try:
            replacement = await message.answer_photo(
                photo=FSInputFile(str(photo_path)),
                caption=text,
                reply_markup=keyboard,
            )
            if replacement is not None:
                await message.delete()
            return
        except Exception:
            logger.debug("[WATERMARK] Could not send photo menu", exc_info=True)

    if message is not None and message.photo:
        try:
            replacement = await message.answer(text, reply_markup=keyboard)
            if replacement is not None:
                await message.delete()
            return
        except Exception:
            logger.debug("[WATERMARK] Could not replace photo menu", exc_info=True)
    await _safe_edit_event(event, text, keyboard)


async def _show_otp_prompt(query, pending):
    digits = pending.get("data", {}).get("digits", "")
    await _edit_query_message(
        query,
        "🔐 Enter the Telegram OTP using the buttons:\n\n"
        f"Code: `{digits or '—'}`",
        _otp_keyboard(digits),
    )


async def _handle_created_clone(result, automation, owner_id):
    """Reload/apply settings after a new clone session is created."""
    if result.get("type") != "clone":
        return
    from privacy_apply_state import clear_clone_policy
    clear_clone_policy(result.get("name", ""))
    from .normal_mode_store import load_active_mode
    if load_active_mode() == "clone":
        if hasattr(automation, "request_reload"):
            automation.request_reload()
        return
    bot = get_bot_client()
    if bot is None:
        return
    try:
        await bot.send_message(
            owner_id,
            f"✅ {result.get('name', 'New clone')} was added.\n\n"
            "Normal Mode is selected. Open Normal Mod and configure "
            "this clone's profile information.\n"
            "This message is shown even when Normal Mode is currently "
            "OFF, so you can prepare the clone settings before enabling it.",
        )
    except Exception:
        logger.debug("[SESSION] Could not send Normal Mode setup notice",
                     exc_info=True)


async def _handle_session_callback(query, data, automation):
    from session_manager import (
        cancel_session_creation,
        complete_session_creation,
    )
    owner_id = query.from_user.id
    pending = get_pending_input(owner_id)

    if data == "session:create:cancel":
        await _edit_query_message(
            query,
            "Are you sure you want to cancel session creation?",
            _session_cancel_confirmation_keyboard(),
        )
        await query.answer()
        return

    if data == "session:create:cancel:no":
        if pending and pending.get("type") == "session:otp":
            await _show_otp_prompt(query, pending)
        elif pending and pending.get("type") == "session:password":
            await _edit_query_message(
                query,
                "🔒 This account has 2FA. Send the 2FA password as a message.",
                make_inline_keyboard([
                    [("❌ Cancel", "session:create:cancel")],
                ]),
            )
        else:
            await _edit_query_message(
                query,
                "➕ Send the phone number with country code.",
                make_inline_keyboard([
                    [("❌ Cancel", "session:create:cancel")],
                ]),
            )
        await query.answer()
        return

    if data == "session:create:cancel:yes":
        if pending and pending.get("type") in {
            "session:phone", "session:otp", "session:password"
        }:
            session_id = pending.get("data", {}).get("session_id")
            if session_id:
                cancel_session_creation(session_id)
        clear_pending_input(owner_id)
        from .menus.session_settings import build_session_settings
        text, keyboard = build_session_settings(
            automation,
            user_id=owner_id,
            page=1,
        )
        await _edit_query_message(query, text, keyboard)
        await query.answer()
        return

    if data == "session:create:choose":
        await _edit_query_message(
            query,
            "➕ Choose the type of session to create:",
            make_inline_keyboard([
                [
                    ("👤 Main", "session:create:main"),
                    ("🧩 Clone", "session:create:clone"),
                ],
                [("❌ Cancel", "session:create:cancel")],
            ]),
        )
        await query.answer()
        return

    if data.startswith("session:create:"):
        session_type = data.rsplit(":", 1)[-1]
        if session_type not in {"main", "clone"}:
            await query.answer("Invalid session type", show_alert=True)
            return
        set_pending_input(owner_id, "session:phone", {
            "session_type": session_type,
        })
        await _edit_query_message(
            query,
            f"➕ Creating {session_type.title()} session.\n"
            "Send the phone number with country code.",
            make_inline_keyboard([
                [("❌ Cancel", "session:create:cancel")],
            ]),
        )
        await query.answer()
        return

    if not pending:
        await query.answer("No active session creation", show_alert=True)
        return

    if pending["type"] != "session:otp":
        await query.answer()
        return

    action = data.rsplit(":", 1)[-1]
    digits = pending["data"].get("digits", "")
    if action.isdigit() and len(digits) < 6:
        pending["data"]["digits"] = digits + action
        await _show_otp_prompt(query, pending)
        await query.answer()
        return
    if action == "back":
        pending["data"]["digits"] = digits[:-1]
        await _show_otp_prompt(query, pending)
        await query.answer()
        return
    if action != "submit":
        await query.answer()
        return
    if len(digits) < 4:
        await query.answer("Enter the complete OTP first", show_alert=True)
        return

    target = (
        {"inline_message_id": query.inline_message_id}
        if query.inline_message_id
        else {
            "chat_id": query.message.chat.id,
            "message_id": query.message.message_id,
        }
        if query.message is not None
        else {}
    )
    phone = pending.get("data", {}).get("phone", "")
    await _edit_query_message(
        query,
        f"📱 Phone: {phone}\nLogging in...🔄\n[░░░░]",
    )
    login_task = asyncio.create_task(
        _animate_session_code(query.bot, target, phone, "Logging in")
    )
    try:
        result = await complete_session_creation(
        pending["data"]["session_id"],
        digits,
        )
    finally:
        login_task.cancel()
        try:
            await login_task
        except asyncio.CancelledError:
            pass
    if result.get("needs_password"):
        pending["type"] = "session:password"
        await _edit_query_message(
            query,
            "🔒 This account has 2FA. Send the 2FA password as a message.",
            make_inline_keyboard([
                [("❌ Cancel", "session:create:cancel")],
            ]),
        )
        await query.answer()
        return
    if result.get("success"):
        clear_pending_input(owner_id)
        await _handle_created_clone(result, automation, owner_id)
        from .menus.session_settings import build_session_settings
        text, keyboard = build_session_settings(
            automation,
            user_id=owner_id,
            page=1,
        )
        await _edit_query_message(query, text, keyboard)
        await query.answer()
    else:
        pending["data"]["digits"] = ""
        await _show_otp_prompt(query, pending)
        await query.answer(
            result.get("message", "Invalid OTP"),
            show_alert=True,
        )


class _MessageAdapter:
    """Expose the small event interface used by the existing action modules."""

    def __init__(self, message: Message, silent: bool = False):
        self._message = message
        self.silent = silent
        self.sender_id = message.from_user.id if message.from_user else None
        self.raw_text = message.text or ""

    async def reply(self, text: str, **kwargs):
        if self.silent:
            return _SilentMessageAdapter()
        return _MessageAdapter(
            await self._message.answer(text, **_message_kwargs(kwargs))
        )

    async def edit(self, text: str, buttons=None):
        return await self._message.edit_text(text, reply_markup=buttons)

    async def answer(self, text: Optional[str] = None, alert: bool = False):
        if self.silent:
            return
        if text:
            return await self._message.answer(text)

    async def delete(self):
        return await self._message.delete()


class _SilentMessageAdapter:
    async def edit(self, text: str, buttons=None):
        return None

    async def delete(self):
        return None


class _CallbackAdapter:
    """Translate legacy action calls to aiogram CallbackQuery operations."""

    def __init__(self, query: CallbackQuery):
        self._query = query
        self.sender_id = query.from_user.id if query.from_user else None

    async def answer(self, text: Optional[str] = None, alert: bool = False):
        await self._query.answer(text=text, show_alert=alert)

    async def edit(self, text: str, buttons=None):
        if self._query.message is not None:
            if self._query.message.photo:
                return await self._query.message.edit_caption(
                    caption=text,
                    reply_markup=buttons,
                )
            return await self._query.message.edit_text(
                text,
                reply_markup=buttons,
            )
        if self._query.inline_message_id:
            return await self._query.bot.edit_message_text(
                inline_message_id=self._query.inline_message_id,
                text=text,
                reply_markup=buttons,
            )
        return None

    async def delete(self):
        
        if self._query.message is not None:
            return await self._query.message.delete()
        return None

    async def reply(self, text: str, **kwargs):
        if self._query.message is not None:
            return _MessageAdapter(
                await self._query.message.answer(text, **_message_kwargs(kwargs))
            )
        await self.answer(text, alert=True)
        return self

    async def reply_photo(self, photo, caption=None):
        if self._query.message is not None:
            return await self._query.message.answer_photo(
                photo=photo,
                caption=caption,
            )
        await self.answer("⚠️ Photo preview is unavailable in inline mode")
        return self


def register_callback_handlers(dispatcher):
    router = Router(name="clone_manager_callbacks")

    @router.callback_query()
    async def _on_callback(query: CallbackQuery):
        global _callback_target
        try:
            user_id = query.from_user.id if query.from_user else None
            data = query.data or ""
            normal_access = can_access_normal_menu(user_id)
            normal_callback = (
                data.startswith("menu:normal")
                or data.startswith("menu:template")
                or data.startswith("action:normal:")
                or data.startswith("action:template:")
            )
            if not is_admin(user_id) and not (normal_access and normal_callback):
                
                
                
                await query.answer()
                return

            try:
                from commander_manager import is_active, is_primary
                if (
                    not is_active(user_id)
                    and not normal_access
                    and not (
                    is_primary(user_id)
                    and (
                        data == "session:commanders"
                        or data.startswith("session:commander:set:")
                        or data == "menu:main"
                    )
                    )
                ):
                    await query.answer()
                    return
            except Exception:
                pass
            automation = get_automation()
            if automation is None:
                await query.answer("⚠️ Bot not ready", show_alert=True)
                return

            logger.info("[CALLBACK] %s", data)
            if query.inline_message_id:
                _callback_target = {
                    "inline_message_id": query.inline_message_id,
                }
            elif query.message is not None:
                _callback_target = {
                    "chat_id": query.message.chat.id,
                    "message_id": query.message.message_id,
                }
            else:
                _callback_target = None

            
            
            
            
            handled = await _route_callback(
                _CallbackAdapter(query),
                data,
                automation,
            )
            if not handled:
                await query.answer("⚠️ Unknown action", show_alert=True)
        except Exception as exc:
            logger.error(
                "[CALLBACK] Error: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            try:
                await query.answer(f"⚠️ Error: {exc}", show_alert=True)
            except Exception:
                logger.debug("[CALLBACK] Failed to report error", exc_info=True)
        finally:
            _callback_target = None

    @router.message()
    async def _on_text_input(message: Message):
        try:
            user_id = message.from_user.id if message.from_user else None
            automation = get_automation()
            admin_id = getattr(automation, "_admin_user_id", None)
            owner_id, pending = get_pending_for_users(
                [user_id, admin_id] if is_admin(user_id) else [user_id]
            )
            normal_access = can_access_normal_menu(user_id)
            if not is_admin(user_id) and not normal_access:
                return
            if not can_operate(user_id) and not normal_access:
                return
            if (
                normal_access
                and pending is not None
                and not (
                    pending["type"].startswith("normal:")
                    or pending["type"].startswith("template:")
                )
            ):
                return
            if pending is not None:
                if _profile_apply_is_active():
                    return
                
                
                
                
                pending["_owner_id"] = owner_id
                if (message.text or "").strip().lower() in (
                    "cancel",
                    "/cancel",
                    "cancel",
                ):
                    clear_pending_input(owner_id)
                    if owner_id != user_id:
                        clear_pending_input(user_id)
                    await message.delete()
                    await _refresh_pending_target(pending, automation)
                    return
                if pending["type"] == "session:phone":
                    from session_manager import start_session_creation
                    phone = (message.text or "").strip()
                    target = pending.get("target") or {}
                    loading_task = asyncio.create_task(
                        _animate_session_code(message.bot, target, phone)
                    )
                    try:
                        await message.delete()
                    except Exception:
                        logger.debug(
                            "[SESSION] Could not delete submitted phone",
                            exc_info=True,
                        )
                    result = await start_session_creation(
                        pending["data"]["session_type"],
                        phone,
                    )
                    loading_task.cancel()
                    try:
                        await loading_task
                    except asyncio.CancelledError:
                        pass
                    if result.get("success"):
                        pending["type"] = "session:otp"
                        pending["data"].update({
                            "session_id": result["session_id"],
                            "digits": "",
                            "phone": phone,
                        })
                        if target.get("chat_id") and target.get("message_id"):
                            await message.bot.edit_message_text(
                                chat_id=target["chat_id"],
                                message_id=target["message_id"],
                                text="🔐 Enter the Telegram OTP using the buttons:\n\n"
                                "Code: `—`",
                                reply_markup=_otp_keyboard(),
                            )
                        else:
                            otp_message = await message.answer(
                                "🔐 Enter the Telegram OTP using the buttons:\n\n"
                                "Code: `—`",
                                reply_markup=_otp_keyboard(),
                            )
                    else:
                        error_text = (
                            f"❌ {result.get('message', 'Could not send OTP')}\n\n"
                            "Send a different phone number or press Cancel."
                        )
                        phone_keyboard = make_inline_keyboard([
                            [("❌ Cancel", "session:create:cancel")],
                        ])
                        if target.get("chat_id") and target.get("message_id"):
                            await message.bot.edit_message_text(
                                chat_id=target["chat_id"],
                                message_id=target["message_id"],
                                text=error_text,
                                reply_markup=phone_keyboard,
                            )
                        elif target.get("inline_message_id"):
                            await message.bot.edit_message_text(
                                inline_message_id=target["inline_message_id"],
                                text=error_text,
                                reply_markup=phone_keyboard,
                            )
                        else:
                            await message.answer(
                                error_text,
                                reply_markup=phone_keyboard,
                            )
                    return
                if pending["type"] == "session:password":
                    from session_manager import complete_session_creation
                    target = pending.get("target") or {}
                    phone = pending.get("data", {}).get("phone", "")
                    loading_text = (
                        f"📱 Phone: {phone}\n"
                        "Logging in...🔄\n"
                        "[░░░░░░░░░░░░░░░░░░░░]"
                    )
                    try:
                        if target.get("chat_id") and target.get("message_id"):
                            await message.bot.edit_message_text(
                                chat_id=target["chat_id"],
                                message_id=target["message_id"],
                                text=loading_text,
                            )
                        elif target.get("inline_message_id"):
                            await message.bot.edit_message_text(
                                inline_message_id=target["inline_message_id"],
                                text=loading_text,
                            )
                    except Exception:
                        logger.debug(
                            "[SESSION] Could not show 2FA login status",
                            exc_info=True,
                        )
                    login_task = asyncio.create_task(
                        _animate_session_code(
                            message.bot,
                            target,
                            phone,
                            "Logging in",
                        )
                    )
                    result = await complete_session_creation(
                        pending["data"]["session_id"],
                        pending["data"]["digits"],
                        password=(message.text or "").strip(),
                    )
                    login_task.cancel()
                    try:
                        await login_task
                    except asyncio.CancelledError:
                        pass
                    if result.get("success"):
                        clear_pending_input(owner_id)
                        await _handle_created_clone(
                            result,
                            automation,
                            owner_id,
                        )
                        await message.delete()
                        from .menus.session_settings import build_session_settings
                        text, keyboard = build_session_settings(
                            automation,
                            user_id=owner_id,
                            page=1,
                        )
                        if target.get("chat_id") and target.get("message_id"):
                            await message.bot.edit_message_text(
                                chat_id=target["chat_id"],
                                message_id=target["message_id"],
                                text=text,
                                reply_markup=keyboard,
                            )
                        elif target.get("inline_message_id"):
                            await message.bot.edit_message_text(
                                inline_message_id=target["inline_message_id"],
                                text=text,
                                reply_markup=keyboard,
                            )
                    else:
                        await message.answer(
                            f"❌ {result.get('message', '2FA verification failed')}"
                        )
                    return
                if pending["type"] in {
                    "bulk:photo",
                    "normal:photo",
                    "profile:photo",
                    "clone_mode:photo",
                    "template:photo",
                }:
                    if (
                        (message.photo or message.video)
                        and (
                            message.reply_to_message is not None
                            or pending.get("bridged")
                            or (
                                pending["type"] in {
                                    "clone_mode:photo",
                                    "normal:photo",
                                    "template:photo",
                                }
                                and getattr(message.chat, "type", None)
                                == "private"
                            )
                        )
                    ):
                        await _handle_photo_input(message, pending)
                elif pending["type"] == "normal:music":
                    if message.audio or message.document:
                        await _handle_music_input(message, pending)
                else:
                    await _handle_text_input(
                        _MessageAdapter(message, silent=True),
                        pending,
                    )
                try:
                    await message.delete()
                except Exception:
                    logger.debug("[TEXT-INPUT] Could not delete input", exc_info=True)
        except Exception as exc:
            logger.error(
                "[TEXT-INPUT] Error: %s: %s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )

    dispatcher.include_router(router)



async def _route_callback(event, data: str, automation) -> bool:
    """Route callback data to the correct menu or action handler."""
    if _profile_apply_is_active():
        await event.answer()
        return True
    if data.startswith("profile_apply:"):
        await _handle_profile_apply_action(event, data, automation)
        return True
    if data.startswith("menu:normal") or data.startswith("action:normal:"):
        from .normal_access import get_access
        delegated = get_access()
        admin_id = getattr(automation, "_admin_user_id", None)
        if (
            delegated is not None
            and str(delegated.get("user_id")) != str(admin_id)
            and is_admin(event.sender_id)
        ):
            name = delegated.get("name") or "Unknown user"
            username = delegated.get("username")
            if username:
                name += f" (@{username})"
            await event.edit(
                "Normal Mode menu access is currently delegated.\n\n"
                "Revoke access from this user first:\n"
                f"Name: {name}\n"
                f"Chat ID: {delegated['user_id']}",
                buttons=make_inline_keyboard([
                    [("Main Menu〽️", "menu:main")],
                ]),
            )
            await event.answer()
            return True
    if data.startswith("go:normal:"):
        from handlers.go_handler import handle_go_callback
        return await handle_go_callback(event, data, automation)
    if data == "session:add_session":
        from .menus.session_settings import build_add_session_menu
        text, keyboard = build_add_session_menu()
        await _show_text_menu(event, text, keyboard)
        await event.answer()
        return True

    if data == "session:commanders":
        from .menus.session_settings import build_commander_menu
        from commander_manager import is_primary
        if not is_primary(event.sender_id):
            await event.answer()
            return True
        text, keyboard = build_commander_menu()
        await _show_text_menu(event, text, keyboard)
        clear_pending_input(event.sender_id)
        await event.answer()
        return True

    if data.startswith("session:commander:set:"):
        from commander_manager import is_primary, set_active
        if not is_primary(event.sender_id):
            await event.answer()
            return True
        name = data.split(":", 3)[3]
        result = set_active(name)
        if not result.get("success"):
            await event.answer(result.get("message", "Switch failed"), alert=True)
            return True
        await event.answer("Commander changed. Reloading...")
        if hasattr(automation, "request_reload"):
            automation.request_reload()
        return True

    if data.startswith("session:create:") or data.startswith("session:otp:"):
        await _handle_session_callback(event._query, data, automation)
        return True

    if data == "menu:main":
        from .menus.main_menu import build_main_menu
        text, keyboard = build_main_menu(
            automation,
            user_id=event.sender_id,
        )
        await _show_text_menu(event, text, keyboard)
        await event.answer()
        return True

    if data == "menu:session_settings" or data.startswith("session:list:"):
        page = 1
        if data.startswith("session:list:"):
            try:
                page = int(data.split(":")[2])
            except (IndexError, ValueError):
                page = 1
        await _show_session_settings(event, automation, page)
        await event.answer()
        return True

    if data.startswith("session:select:"):
        await event.answer("Reply with the clone number.", alert=True)
        return True

    if data == "session:activate_prompt":
        await _show_session_settings(event, automation)
        await event.answer("Reply with the clone number.", alert=True)
        return True

    if data == "session:activation_status":
        from .menus.session_settings import build_activation_status
        text, keyboard = build_activation_status()
        await _show_text_menu(event, text, keyboard)
        clear_pending_input(event.sender_id)
        await event.answer()
        return True

    if data == "session:add_session":
        await event.answer()
        return True

    if data.startswith("session:confirm:"):
        parts = data.split(":")
        try:
            number = int(parts[2])
        except (IndexError, ValueError):
            await event.answer("Invalid clone number.", alert=True)
            return True
        action = parts[3] if len(parts) > 3 else ""
        if action == "disable":
            from session_manager import rename_clone_slot
            try:
                rename_clone_slot(number, enabled=False)
                if hasattr(automation, "request_reload"):
                    automation.request_reload()
                await _show_session_settings(event, automation)
                await event.answer("Session disabled.")
            except Exception as exc:
                await event.answer(f"Could not disable session: {exc}", alert=True)
            return True
        if action == "enable":
            from session_manager import rename_clone_slot
            try:
                rename_clone_slot(number, enabled=True, target_number=number)
                if hasattr(automation, "request_reload"):
                    automation.request_reload()
                await _show_session_settings(event, automation)
                await event.answer("Session enabled.")
            except Exception as exc:
                await event.answer(f"Could not enable session: {exc}", alert=True)
            return True
        await _show_session_settings(event, automation)
        await event.answer()
        return True

    if data == "session:cancel":
        clear_pending_input(event.sender_id)
        await _show_session_settings(event, automation)
        await event.answer()
        return True

    if data.startswith("session:enable:"):
        from session_manager import rename_clone_slot
        parts = data.split(":")
        try:
            source_number = int(parts[2])
            target_number = int(parts[3])
            rename_clone_slot(
                source_number,
                enabled=True,
                target_number=target_number,
            )
            if hasattr(automation, "request_reload"):
                automation.request_reload()
            await _show_session_settings(event, automation)
            await event.answer("Session enabled.")
        except (IndexError, ValueError, OSError) as exc:
            await event.answer(f"Could not enable session: {exc}", alert=True)
        return True

    if data == "session:repair":
        clear_pending_input(event.sender_id)
        try:
            await _run_session_repair(event, automation)
            await event.answer("Session repair completed.")
        except Exception as exc:
            logger.error("[SESSION-REPAIR] Failed: %s", exc, exc_info=True)
            await event.answer("Session repair failed.", alert=True)
        return True

    if data == "menu:clone_mode":
        await _edit_clone_mode_menu(event, automation)
        await event.answer()
        return True

    if data == "menu:clone_list" or data.startswith("page:clone_list:"):
        from .menus.clone_list import build_clone_list
        page = 1
        if data.startswith("page:clone_list:"):
            try:
                page = int(data.split(":")[2])
            except (IndexError, ValueError):
                page = 1
        text, keyboard = build_clone_list(automation, page=page)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data.startswith("menu:clone:"):
        from .menus.clone_detail import build_clone_detail
        try:
            clone_idx = int(data.split(":")[2])
        except (IndexError, ValueError):
            await event.answer("⚠️ Invalid clone index", alert=True)
            return True
        text, keyboard = build_clone_detail(automation, clone_idx)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data in {"menu:status", "status:refresh"}:
        from .menus.status_menu import build_status_menu
        text, keyboard = await build_status_menu(automation)
        try:
            await event.edit(text, buttons=keyboard)
        except Exception as exc:
            
            
            
            if "message is not modified" not in str(exc).lower():
                raise
        await event.answer("🔄 Refreshed" if data == "status:refresh" else None)
        return True

    if data == "menu:config":
        from .menus.config_menu import build_config_menu
        text, keyboard = build_config_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data == "menu:privacy_main":
        from .menus.privacy_menu import build_privacy_main_menu
        text, keyboard = build_privacy_main_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data.startswith("menu:privacy:"):
        from .menus.privacy_menu import build_privacy_clone_menu
        try:
            clone_idx = int(data.split(":")[2])
        except (IndexError, ValueError):
            await event.answer("⚠️ Invalid clone index", alert=True)
            return True
        text, keyboard = build_privacy_clone_menu(automation, clone_idx)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data == "menu:profile_main":
        from .menus.profile_menu import build_profile_main_menu
        text, keyboard = build_profile_main_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data.startswith("menu:profile:"):
        from .menus.profile_menu import build_profile_clone_menu
        try:
            clone_idx = int(data.split(":")[2])
        except (IndexError, ValueError):
            await event.answer("⚠️ Invalid clone index", alert=True)
            return True
        text, keyboard = build_profile_clone_menu(automation, clone_idx)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data in {"menu:bulk", "menu:clone_mod"}:
        from .menus.bulk_actions import build_bulk_menu
        text, keyboard = build_bulk_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data == "menu:safety":
        from .menus.safety_menu import build_safety_menu
        text, keyboard = build_safety_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data.startswith("action:safety:"):
        await _handle_safety_action(event, data, automation)
        return True

    if data == "menu:analytics":
        from .menus.analytics_menu import build_analytics_menu
        text, keyboard = build_analytics_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return True

    if data == "menu:normal_main":
        from .menus.normal_mode import build_normal_main_menu
        delegated = can_access_normal_menu(event.sender_id) and not is_admin(
            event.sender_id
        )
        text, keyboard = build_normal_main_menu(
            automation,
            delegated=delegated,
        )
        await _edit_normal_menu(
            event,
            automation,
            1,
            inline=event._query.inline_message_id is not None,
        )
        await event.answer()
        return True

    if data == "menu:template":
        from .menus.template import build_template_list_menu
        delegated = can_access_normal_menu(event.sender_id) and not is_admin(
            event.sender_id
        )
        text, keyboard = build_template_list_menu(delegated=delegated)
        await _show_text_menu(event, text, keyboard)
        await event.answer()
        return True

    if data.startswith("menu:template:open:"):
        template_name = data.split(":", 3)[3]
        await _edit_template_menu(event, automation, template_name, 1)
        await event.answer()
        return True

    if data.startswith("menu:template:edit:"):
        _, _, _, template_name, clone_idx = data.split(":", 4)
        await _edit_template_menu(event, automation, template_name, int(clone_idx))
        await event.answer()
        return True

    if data.startswith("menu:template:edit_info:"):
        _, _, _, template_name, clone_idx = data.split(":", 4)
        from .menus.template import build_template_menu
        total = len(getattr(automation, "clone_clients", []))
        clone_idx = int(clone_idx)
        previous = total if clone_idx <= 1 else clone_idx - 1
        following = 1 if clone_idx >= total else clone_idx + 1
        await event.edit(
            f"Template: {template_name}\nClone {clone_idx} settings:",
            buttons=make_inline_keyboard([
                [
                    ("⏮Back", f"menu:template:edit_info:{template_name}:{previous}"),
                    (f"Clone{clone_idx}👤", "noop"),
                    ("Next⏭", f"menu:template:edit_info:{template_name}:{following}"),
                ],
                [
                    ("First Name✏️", f"action:template:field:{template_name}:{clone_idx}:first_name"),
                    ("Last Name✏️", f"action:template:field:{template_name}:{clone_idx}:last_name"),
                ],
                [("Bio💬", f"action:template:field:{template_name}:{clone_idx}:bio")],
                [("SetPic☣", f"action:template:field:{template_name}:{clone_idx}:photo")],
                [("💾Back&Save🔙", f"menu:template:edit:{template_name}:{clone_idx}")],
            ]),
        )
        await event.answer()
        return True

    if data.startswith("menu:normal:"):
        from .menus.normal_mode import build_normal_clone_menu
        try:
            clone_idx = int(data.split(":")[2])
        except (IndexError, ValueError):
            await event.answer("⚠️ Invalid clone index", alert=True)
            return True
        text, keyboard = build_normal_clone_menu(automation, clone_idx)
        await _edit_normal_menu(
            event,
            automation,
            clone_idx,
            inline=event._query.inline_message_id is not None,
        )
        await event.answer()
        return True

    if data.startswith("action:privacy:"):
        from .actions.privacy_actions import handle_privacy_action
        await handle_privacy_action(event, data, automation)
        return True

    if data.startswith("action:profile:"):
        from .actions.profile_actions import handle_profile_action
        await handle_profile_action(event, data, automation, set_pending_input)
        return True

    if data.startswith("action:bulk:"):
        from .actions.bulk_actions import handle_bulk_action
        await handle_bulk_action(event, data, automation, set_pending_input)
        return True

    if data.startswith("action:clone_mode:"):
        await _handle_clone_mode_action(event, data, automation)
        return True

    if data == "action:clone:toggle":
        from .profile_modes import switch_mode
        target_mode = (
            "normal"
            if not getattr(automation, "normal_mode", False)
            else "clone"
        )
        results = await switch_mode(automation, target_mode)
        from .menus.bulk_actions import build_bulk_menu
        text, keyboard = build_bulk_menu(automation)
        text += (
            f"\n\nApplied: {results['success']} | "
            f"Skipped: {results['skipped']} | Failed: {results['failed']}"
        )
        await event.edit(text, buttons=keyboard)
        await event.answer(f"Active mode: {target_mode.title()}")
        return True

    if data.startswith("action:group:"):
        from .actions.group_actions import handle_group_action
        await handle_group_action(event, data, automation, set_pending_input)
        return True

    if data.startswith("action:loop:"):
        from .actions.loop_actions import handle_loop_action
        await handle_loop_action(event, data, automation)
        return True

    if data.startswith("action:normal:"):
        await _handle_normal_action(event, data, automation)
        return True

    if data.startswith("action:template:"):
        parts = data.split(":")
        if len(parts) >= 3 and parts[2] == "add":
            set_pending_input(event.sender_id, "template:add", {})
            await event.edit(
                "Send a name for the new template.",
                buttons=make_inline_keyboard([[
                    ("Cancel", "menu:normal_main"),
                ]]),
            )
            await event.answer()
            return True
        if len(parts) >= 3 and parts[2] == "apply":
            template_name = ":".join(parts[3:]) or ""
            from .normal_mode_store import load_settings, save_clone_field
            settings = load_settings()
            template = settings["template"]["templates"].get(template_name)
            if template is None:
                await event.answer("Template not found.", alert=True)
                return True
            for clone_key, profile in template.get("clones", {}).items():
                clone_idx = int(clone_key)
                for field in ("first_name", "last_name", "bio"):
                    if field in profile:
                        save_clone_field(clone_idx, field, profile[field])
                photo_name = profile.get("photo")
                if photo_name:
                    source = (
                        __import__("pathlib").Path(
                            __file__
                        ).resolve().parent
                        / "normal_mode_data"
                        / "template"
                        / template_name
                        / photo_name
                    )
                    if source.is_file():
                        from .normal_mode_store import save_normal_photo
                        save_normal_photo(clone_idx, str(source))
            await event.answer("Template applied to Normal Mode.")
            from .menus.template import build_template_menu
            text, keyboard = build_template_menu(automation, template_name, 1)
            await event.edit(text, buttons=keyboard)
            return True
        if len(parts) >= 3 and parts[2] == "field":
            _, _, _, template_name, clone_idx, action = data.split(":", 5)
            if action == "photo":
                input_type = "template:photo"
            else:
                input_type = f"template:{action}"
            set_pending_input(
                event.sender_id,
                input_type,
                {"clone_idx": int(clone_idx), "template_name": template_name},
            )
            await event.edit(
                f"Send template {action.replace('_', ' ')} for Clone #{clone_idx}.",
                buttons=make_inline_keyboard([[
                    ("Cancel", f"menu:template:edit:{template_name}:{clone_idx}"),
                ]]),
            )
            await event.answer()
            return True
        return True

    if data == "action:refresh":
        from .menus.main_menu import build_main_menu
        text, keyboard = build_main_menu(
            automation,
            user_id=event.sender_id,
        )
        await event.edit(text, buttons=keyboard)
        await event.answer("🔄 Refreshed")
        return True

    if data == "action:close":
        try:
            await event.delete()
        except Exception:
            pass
        await event.answer()
        return True

    if data == "noop":
        await event.answer()
        return True

    return False


async def _handle_clone_mode_action(event, data: str, automation):
    global _profile_apply_active
    action = data.split(":", 2)[-1]

    if action == "toggle":
        from .profile_modes import save_active_mode
        from .normal_mode_store import load_active_mode
        target = "normal" if load_active_mode() == "clone" else "clone"
        save_active_mode(target)
        automation.normal_mode = target == "normal"
        await _edit_clone_mode_menu(event, automation)
        await event.answer(f"Clone Mod: {'ON🟢' if target == 'clone' else 'OFF🟥'}")
        return

    if action == "watermark":
        from .normal_mode_store import load_settings, save_clone_mode_field
        enabled = not bool(load_settings()["clone"].get("watermark", False))
        save_clone_mode_field("watermark", enabled)
        await _edit_clone_mode_menu(event, automation)
        await event.answer()
        return

    if action == "wm_settings":
        from .normal_mode_store import load_watermark_settings
        _watermark_drafts[event.sender_id] = load_watermark_settings().copy()
        await _edit_watermark_menu(event, event.sender_id)
        await event.answer()
        return

    if action == "wm_cancel":
        clear_pending_input(event.sender_id)
        await _edit_watermark_menu(event, event.sender_id)
        await event.answer()
        return

    if action == "wm_discard_yes":
        clear_pending_input(event.sender_id)
        _watermark_drafts.pop(event.sender_id, None)
        await _edit_clone_mode_menu(event, automation)
        await event.answer()
        return

    if action == "wm_discard_no":
        await _edit_watermark_menu(event, event.sender_id)
        await event.answer()
        return

    if action == "wm_reset":
        from .normal_mode_store import _default_watermark_settings
        _watermark_drafts[event.sender_id] = _default_watermark_settings()
        await _edit_watermark_menu(event, event.sender_id)
        await event.answer("WaterMark draft reset")
        return

    if action == "wm_save":
        from .normal_mode_store import save_watermark_settings
        draft = _watermark_menu_data(event.sender_id)
        save_watermark_settings(draft)
        clear_pending_input(event.sender_id)
        _watermark_drafts.pop(event.sender_id, None)
        await _edit_clone_mode_menu(event, automation)
        await event.answer()
        return

    if action.startswith("wm_toggle:"):
        from .normal_mode_store import load_watermark_settings
        field = action.split(":", 1)[1]
        current = _watermark_menu_data(event.sender_id)
        if field == "orientation":
            value = "vertical" if current.get(field) == "horizontal" else "horizontal"
        else:
            value = "text" if current.get(field, "full") == "full" else "full"
        current[field] = value
        await _edit_watermark_menu(event, event.sender_id)
        await event.answer()
        return

    if action.startswith("wm_input:"):
        field = action.split(":", 1)[1]
        labels = {
            "text": "WaterMark text (you can use {NAME} or {N})",
            "x": "X position percentage (0-100)",
            "y": "Y position percentage (0-100)",
            "angle": "Angle in degrees (0-359)",
            "opacity": "Opacity percentage (0-100)",
            "darkness": "Blackness percentage (0-100)",
        }
        set_pending_input(event.sender_id, f"clone_mode:wm:{field}", {})
        from .utils.keyboards import make_inline_keyboard
        await event.edit(
            f"Send the {labels.get(field, field)}.",
            buttons=make_inline_keyboard([
                [("🔙Back&Cancel❌", "action:clone_mode:wm_cancel")]
            ]),
        )
        await event.answer()
        return

    if action == "set_photo":
        set_pending_input(event.sender_id, "clone_mode:photo", {})
        from .utils.keyboards import make_inline_keyboard
        await event.edit(
            "📸 Send the profile photo and reply to this message.\n"
            "The uploaded message will be deleted after download.",
            buttons=make_inline_keyboard([
                [("Cancel", "action:clone_mode:cancel")]
            ]),
        )
        await event.answer()
        return

    if action == "cancel":
        clear_pending_input(event.sender_id)
        await _edit_clone_mode_menu(event, automation)
        await event.answer()
        return

    if action in {"edit_bio", "edit_fname", "edit_lname"}:
        input_type = {
            "edit_bio": "clone_mode:bio",
            "edit_fname": "clone_mode:name",
            "edit_lname": "clone_mode:lname",
        }[action]
        set_pending_input(event.sender_id, input_type, {})
        from .utils.keyboards import make_inline_keyboard
        inline_prompt = bool(
            getattr(getattr(event, "_query", None), "inline_message_id", None)
        )
        verb = "Reply to this message with" if inline_prompt else "Send"
        prompts = {
            "edit_bio": f"{verb} the shared bio (maximum 70 characters).",
            "edit_fname": (
                f"{verb} the shared first name (maximum 64 characters). "
                "Use {N} for the Clone number."
            ),
            "edit_lname": f"{verb} the shared last name (maximum 64 characters).",
        }
        await event.edit(
            prompts[action],
            buttons=make_inline_keyboard([
                [("Cancel", "action:clone_mode:cancel")]
            ]),
        )
        await event.answer()
        return

    if action == "apply":
        await _show_profile_apply_selector(event, automation, "clone", 1)
        return

    await event.answer("⚠️ Unknown Clone Mod action", alert=True)


async def _show_profile_apply_selector(event, automation, mode, clone_idx):
    """Replace the mode menu with an explicit Apply target selector."""
    owner_id = event.sender_id
    set_pending_input(
        owner_id,
        "profile:apply_select",
        {
            "mode": mode,
            "anchor_clone": int(clone_idx or 1),
        },
    )
    pending = get_pending_input(owner_id)
    
    pending["target"] = {}
    total = len(getattr(automation, "clone_clients", []))
    keyboard = _profile_apply_keyboard(mode, clone_idx, total)
    await event.delete()
    prompt = await event.reply(
        "What should be applied?\n"
        "Send clone number(s) or use the buttons below.",
        buttons=keyboard,
    )
    prompt_message = getattr(prompt, "_message", None)
    if prompt_message is not None:
        pending["data"].update({
            "prompt_chat_id": prompt_message.chat.id,
            "prompt_message_id": prompt_message.message_id,
        })
    await event.answer()


async def _handle_text_input(event, pending: dict):
    """Handle messages received while an admin input is pending."""
    automation = get_automation()
    if automation is None:
        return

    input_type = pending["type"]
    input_data = pending.get("data", {})
    user_id = event.sender_id
    owner_id = pending.get("_owner_id", user_id)
    text = (event.raw_text or "").strip()
    if not text:
        return

    if text.lower() in ("cancel", "/cancel"):
        clear_pending_input(owner_id)
        if owner_id != user_id:
            clear_pending_input(user_id)
        if input_type.startswith("clone_mode:wm:"):
            await _refresh_pending_target(pending, automation)
        else:
            await event.reply("❌ Cancelled")
        return

    try:
        if input_type == "profile:apply_select":
            total = len(getattr(automation, "clone_clients", []))
            try:
                indices = _parse_clone_selection(text, total)
            except ValueError as exc:
                pending["_keep"] = True
                target = input_data
                await event._message.answer(
                    f"⚠️ {exc}\nSend clone number(s) again, for example: `1-5 10 17`"
                )
                return

            prompt_chat_id = input_data.get("prompt_chat_id")
            prompt_message_id = input_data.get("prompt_message_id")
            if prompt_chat_id and prompt_message_id:
                try:
                    await event._message.bot.delete_message(
                        chat_id=prompt_chat_id,
                        message_id=prompt_message_id,
                    )
                except Exception:
                    logger.debug("[PROFILE-APPLY] Could not delete selector", exc_info=True)

            mode = input_data.get("mode", "normal")
            anchor_clone = int(input_data.get("anchor_clone", indices[0]))
            global _profile_apply_active
            _profile_apply_active = True
            try:
                status = await event._message.answer(
                    "In progress\nStarting...\n" + _progress_bar(0)
                )
                results = await _apply_with_progress(
                    _MessageAdapter(status),
                    automation,
                    mode,
                    force=True,
                    clone_indices=indices,
                )
                if mode == "normal":
                    from .menus.normal_mode import build_normal_clone_menu
                    menu_text, menu_keyboard = build_normal_clone_menu(
                        automation, anchor_clone
                    )
                else:
                    from .menus.clone_mode import build_clone_mode_menu
                    menu_text, menu_keyboard = build_clone_mode_menu(automation)
                await status.edit_text(
                    menu_text,
                    reply_markup=menu_keyboard,
                )
                logger.info(
                    "[PROFILE-APPLY] %s selected=%s success=%s skipped=%s failed=%s",
                    mode, indices, results["success"], results["skipped"],
                    results["failed"],
                )
            finally:
                _profile_apply_active = False
        elif input_type == "profile:name":
            from .actions.profile_actions import complete_name_change
            await complete_name_change(event, text, input_data, automation)
        elif input_type == "normal:name":
            from .actions.profile_actions import complete_name_change
            await complete_name_change(event, text, input_data, automation)
        elif input_type == "template:add":
            from .normal_mode_store import create_template
            if not create_template(text[:64]):
                await event.reply("Template name is empty or already exists.")
                return
            await _refresh_pending_target(
                {"target": pending.get("target"), "type": "template:list",
                 "_owner_id": owner_id},
                automation,
            )
        elif input_type.startswith("template:") and input_type != "template:photo":
            from .normal_mode_store import save_template_field
            field = input_type.split(":", 1)[1]
            save_template_field(
                int(input_data["clone_idx"]),
                field,
                text[:70 if field == "bio" else 64],
                input_data.get("template_name", "Default"),
            )
            await _refresh_pending_target(pending, automation)
        elif input_type == "profile:bio":
            from .actions.profile_actions import complete_bio_change
            await complete_bio_change(event, text, input_data, automation)
        elif input_type == "normal:bio":
            from .actions.profile_actions import complete_bio_change
            await complete_bio_change(event, text, input_data, automation)
        elif input_type == "profile:username":
            from .actions.profile_actions import complete_username_change
            await complete_username_change(event, text, input_data, automation)
        elif input_type == "bulk:name":
            from .actions.bulk_actions import complete_bulk_name
            await complete_bulk_name(event, text, automation)
        elif input_type == "bulk:name_template":
            from .actions.bulk_actions import complete_bulk_name_template
            await complete_bulk_name_template(event, text, automation)
        elif input_type == "bulk:name_number_base":
            if not text:
                await event.reply("⚠️ Base name cannot be empty")
            else:
                input_data["base_name"] = text
                pending["type"] = "bulk:name_number_position"
                pending["_keep"] = True
                await event.reply(
                    "Choose number position:\n"
                    "1 — after the name: Clone1\n"
                    "2 — before the name: 1Clone"
                )
        elif input_type == "bulk:name_number_position":
            from .actions.bulk_actions import complete_bulk_name_number
            await complete_bulk_name_number(
                event,
                input_data.get("base_name", ""),
                text,
                automation,
            )
        elif input_type == "bulk:bio":
            from .actions.bulk_actions import complete_bulk_bio
            await complete_bulk_bio(event, text, automation)
        elif input_type == "clone_mode:name":
            from .normal_mode_store import (
                clear_clone_mode_overrides,
                save_clone_mode_field,
            )
            
            
            clear_clone_mode_overrides("first_name")
            save_clone_mode_field("first_name", text[:64])
            await event.reply("✅ Clone Mode name saved. Press Apply⚙ to apply it.")
        elif input_type == "clone_mode:lname":
            from .normal_mode_store import save_clone_mode_field
            save_clone_mode_field("last_name", text[:64])
            await event.reply("✅ Clone Mode last name saved. Press Apply⚙ to apply it.")
        elif input_type == "clone_mode:bio":
            from .normal_mode_store import save_clone_mode_field
            save_clone_mode_field("bio", text[:70])
            await event.reply("✅ Clone Mode bio saved. Press Apply⚙ to apply it.")
        elif input_type == "session:toggle_number":
            from session_manager import list_session_slots, session_repair_was_run
            try:
                number = int(text)
            except ValueError:
                await event.reply("Please reply with a valid clone number.")
                return
            slot = next(
                (item for item in list_session_slots()
                 if item["number"] == number),
                None,
            )
            if slot is None:
                await event.reply("Clone number not found.")
                return
            pending["data"]["number"] = number
            pending["data"]["enabled"] = slot["active"]
            if not slot["active"] and session_repair_was_run():
                from session_manager import get_next_clone_number
                pending["type"] = "session:activation_choice"
                pending["data"]["new_number"] = get_next_clone_number()
            elif not slot["active"]:
                from session_manager import rename_clone_slot
                rename_clone_slot(number, enabled=True, target_number=number)
                if hasattr(automation, "request_reload"):
                    automation.request_reload()
                clear_pending_input(owner_id)
                await _refresh_pending_target(pending, automation)
                return
            else:
                pending["type"] = "session:confirmation"
            pending["_keep"] = True
            await _refresh_pending_target(pending, automation)
            return
        elif input_type.startswith("clone_mode:wm:"):
            field = input_type.rsplit(":", 1)[-1]
            try:
                value = int(text)
            except ValueError:
                await event.reply("⚠️ Please send a number.")
                return
            limits = {
                "x": (0, 100),
                "y": (0, 100),
                "angle": (0, 359),
                "opacity": (0, 100),
                "darkness": (0, 100),
            }
            if field == "text":
                _watermark_draft(owner_id)["text"] = text[:128]
                return
            low, high = limits[field]
            if not low <= value <= high:
                await event.reply(f"⚠️ Value must be between {low} and {high}.")
                return
            _watermark_draft(owner_id)[field] = value
        elif input_type == "bulk:privacy_target":
            from .actions.privacy_actions import complete_bulk_privacy_target
            await complete_bulk_privacy_target(
                event,
                text,
                input_data.get("mode", "allow"),
                automation,
            )
        elif input_type == "group:join":
            from .actions.group_actions import complete_join_group
            await complete_join_group(event, text, automation)
        elif input_type == "group:leave":
            from .actions.group_actions import complete_leave_group
            await complete_leave_group(event, text, automation)
        else:
            await event.reply(f"❓ Unknown input type: `{input_type}`\nInput cleared.")
    except Exception as exc:
        logger.error("[TEXT-INPUT] Handler error: %s", exc, exc_info=True)
        await event.reply(f"⚠️ Error: {exc}")
    finally:
        keep_pending = pending.pop("_keep", False)
        if not keep_pending:
            clear_pending_input(owner_id)
            if owner_id != user_id:
                clear_pending_input(user_id)
            await _refresh_pending_target(pending, automation)


async def _handle_normal_action(event, data: str, automation):
    global _profile_apply_active
    parts = data.split(":")
    if len(parts) < 3:
        await event.answer("⚠️ Invalid Normal Mode action", alert=True)
        return

    action = parts[2]
    if action == "toggle":
        from .normal_mode_store import save_active_mode
        target_mode = (
            "clone"
            if getattr(automation, "normal_mode", False)
            else "normal"
        )
        
        
        
        save_active_mode(target_mode)
        automation.normal_mode = target_mode == "normal"
        from .menus.normal_mode import build_normal_main_menu
        text, keyboard = build_normal_main_menu(automation)
        await event.edit(text, buttons=keyboard)
        await event.answer(
            f"{target_mode.title()} selected. Press Apply to apply profiles."
        )
        return

    if action == "cancel":
        clear_pending_input(event.sender_id)
        from .menus.normal_mode import build_normal_clone_menu
        clone_idx = int(parts[1]) if len(parts) > 1 else 1
        text, keyboard = build_normal_clone_menu(automation, clone_idx)
        await event.edit(text, buttons=keyboard)
        await event.answer()
        return

    if len(parts) < 4:
        await event.answer("⚠️ Invalid Clone action", alert=True)
        return

    try:
        clone_idx = int(parts[2])
    except ValueError:
        await event.answer("⚠️ Invalid clone index", alert=True)
        return

    action = parts[3]
    if action == "cancel":
        clear_pending_input(event.sender_id)
        from .menus.normal_mode import build_normal_clone_menu
        text, keyboard = build_normal_clone_menu(automation, clone_idx)
        await _safe_edit_event(event, text, keyboard)
        await event.answer()
        return

    if action in {"first_name", "last_name"}:
        from .utils.keyboards import make_inline_keyboard
        set_pending_input(
            event.sender_id,
            "normal:name",
            {
                "clone_idx": clone_idx,
                "field": action,
                "normal_mode": True,
            },
        )
        await event.edit(
            f"✏️ **Normal Mode — Clone #{clone_idx}**\n\n"
            f"Send the new {'first name' if action == 'first_name' else 'last name'}.\n"
            f"{'Reply to this message' if getattr(getattr(event, '_query', None), 'inline_message_id', None) else 'Send it as a new message'} "
            "(maximum 64 characters).\n",
            buttons=make_inline_keyboard([
                [("Cancel", f"action:normal:{clone_idx}:cancel")]
            ]),
        )
        await event.answer()
        return

    if action == "bio":
        from .utils.keyboards import make_inline_keyboard
        set_pending_input(
            event.sender_id,
            "normal:bio",
            {"clone_idx": clone_idx, "normal_mode": True},
        )
        await event.edit(
            f"📝 **Normal Mode — Clone #{clone_idx} Bio**\n\n"
            f"{'Reply to this message' if getattr(getattr(event, '_query', None), 'inline_message_id', None) else 'Send it as a new message'} "
            "(maximum 70 characters).\n",
            buttons=make_inline_keyboard([
                [("Cancel", f"action:normal:{clone_idx}:cancel")]
            ]),
        )
        await event.answer()
        return

    if action == "apply":
        await _show_profile_apply_selector(event, automation, "normal", clone_idx)
        return

    if action == "photo":
        from .utils.keyboards import make_inline_keyboard
        set_pending_input(
            event.sender_id,
            "normal:photo",
            {"clone_idx": clone_idx, "normal_mode": True},
        )
        await event.edit(
            f"📸 **Normal Mode — Clone #{clone_idx} Photo**\n\n"
            "Send an image or video.",
            buttons=make_inline_keyboard([
                [("Cancel", f"action:normal:{clone_idx}:cancel")]
            ]),
        )
        await event.answer()
        return

    if action == "music":
        set_pending_input(
            event.sender_id,
            "normal:music",
            {"clone_idx": clone_idx, "normal_mode": True},
        )
        await event.edit(
            f"🎵 **Normal Mode — Clone #{clone_idx} Music**\n\n"
            "Send or reply with an audio file (.mp3/.m4a).\n"
            "The previous file will be replaced.\n"
            "Send `cancel` to abort."
        )
        await event.answer()
        return

    await event.answer("❓ Unknown Normal Mode action", alert=True)


async def _handle_safety_action(event, data: str, automation):
    """Handle Safety menu actions without exposing a second admin API."""
    action = data.split(":", 2)[-1]

    if action == "check_all":
        checked = 0
        online = 0
        for client in automation.clone_clients:
            checked += 1
            try:
                if not client.is_connected():
                    await client.connect()
                if client.is_connected():
                    online += 1
            except Exception:
                pass
        from .menus.safety_menu import build_safety_menu
        text, keyboard = build_safety_menu(automation)
        text += (
            f"\n\n✅ Session check complete\n"
            f"Checked: {checked} | Online: {online}"
        )
        await event.edit(text, buttons=keyboard)
        await event.answer("Session check complete")
        return

    if action == "reconnect":
        reconnected = 0
        failed = 0
        for client in automation.clone_clients:
            try:
                if not client.is_connected():
                    await client.connect()
                if client.is_connected():
                    reconnected += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        from .menus.safety_menu import build_safety_menu
        text, keyboard = build_safety_menu(automation)
        text += (
            f"\n\n🔄 Reconnect complete\n"
            f"Connected: {reconnected} | Failed: {failed}"
        )
        await event.edit(text, buttons=keyboard)
        await event.answer("Reconnect complete")
        return

    if action == "dead_list":
        import glob
        import os
        from config import SESSIONS_DIR

        dead_dir = os.path.join(SESSIONS_DIR, "_dead")
        files = sorted(
            os.path.basename(path)
            for path in glob.glob(os.path.join(dead_dir, "*.session"))
        )
        if files:
            body = "\n".join(f"• `{name}`" for name in files[:50])
            if len(files) > 50:
                body += f"\n… and {len(files) - 50} more"
        else:
            body = "No quarantined sessions."
        from .menus.safety_menu import build_safety_menu
        _, keyboard = build_safety_menu(automation)
        await event.edit(
            f"💀 **Dead Sessions**\n\n{body}",
            buttons=keyboard,
        )
        await event.answer()
        return

    await event.answer(f"❓ Unknown Safety action: {action}", alert=True)


async def _handle_profile_apply_action(event, data: str, automation):
    """Handle Apply target buttons after the original menu was deleted."""
    parts = data.split(":")
    if len(parts) != 4:
        await event.answer("⚠️ Invalid Apply action", alert=True)
        return
    _, target, mode, raw_clone = parts
    if mode not in {"clone", "normal"} or target not in {"all", "one", "back"}:
        await event.answer("⚠️ Invalid Apply action", alert=True)
        return
    try:
        anchor_clone = int(raw_clone)
    except ValueError:
        anchor_clone = 1

    if target == "back":
        clear_pending_input(event.sender_id)
        await event.delete()
        if mode == "normal":
            from .menus.normal_mode import build_normal_clone_menu
            text, keyboard = build_normal_clone_menu(automation, anchor_clone)
        else:
            from .menus.clone_mode import build_clone_mode_menu
            text, keyboard = build_clone_mode_menu(automation)
        await event.reply(text, buttons=keyboard)
        await event.answer()
        return

    total = len(getattr(automation, "clone_clients", []))
    indices = list(range(1, total + 1)) if target == "all" else [anchor_clone]
    if not indices or any(index < 1 or index > total for index in indices):
        await event.answer("⚠️ Clone not found", alert=True)
        return

    clear_pending_input(event.sender_id)
    global _profile_apply_active
    _profile_apply_active = True
    try:
        await event.delete()
        status = await event.reply("In progress\nStarting...\n" + _progress_bar(0))
        results = await _apply_with_progress(
            status,
            automation,
            mode,
            force=True,
            clone_indices=indices,
        )
        if mode == "normal":
            from .menus.normal_mode import build_normal_clone_menu
            menu_text, menu_keyboard = build_normal_clone_menu(
                automation, anchor_clone
            )
        else:
            from .menus.clone_mode import build_clone_mode_menu
            menu_text, menu_keyboard = build_clone_mode_menu(automation)
        await status.edit(menu_text, buttons=menu_keyboard)
        await event.answer(
            f"Applied: {results['success']} | Skipped: {results['skipped']} | "
            f"Failed: {results['failed']}"
        )
    finally:
        _profile_apply_active = False


async def _handle_photo_input(message: Message, pending: dict):
    """Download an image/video and save or apply it through Telethon."""
    from profile_manager import set_profile_media

    temp_path = None
    try:
        media = message.photo[-1] if message.photo else message.video
        if media is None:
            return
        file = await message.bot.get_file(media.file_id)
        suffix = os.path.splitext(file.file_path or "")[1]
        if not suffix:
            suffix = ".mp4" if message.video else ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = temp.name

        await message.bot.download_file(file.file_path, destination=temp_path)
        automation = get_automation()
        input_type = pending["type"]
        results = {"success": 0, "failed": 0, "skipped": 0}

        if input_type == "normal:photo":
            clone_idx = pending["data"].get("clone_idx")
            zero_idx = clone_idx - 1
            if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
                logger.warning("[PHOTO] Clone #%s not found", clone_idx)
                return
            client = automation.clone_clients[zero_idx]
            name = automation.clone_names[zero_idx]
            from .normal_mode_store import save_normal_photo
            saved_path = save_normal_photo(clone_idx, temp_path)
            return

        if input_type == "template:photo":
            clone_idx = pending["data"].get("clone_idx")
            from .normal_mode_store import save_template_photo
            save_template_photo(
                clone_idx,
                temp_path,
                pending["data"].get("template_name", "Default"),
            )
            return

        if input_type == "clone_mode:photo":
            from .normal_mode_store import save_mode_photo
            saved_path = save_mode_photo("clone", temp_path)
            return

        if input_type == "profile:photo":
            clone_idx = pending["data"].get("clone_idx")
            zero_idx = clone_idx - 1
            if zero_idx < 0 or zero_idx >= len(automation.clone_clients):
                logger.warning("[PHOTO] Clone #%s not found", clone_idx)
                return
            client = automation.clone_clients[zero_idx]
            name = automation.clone_names[zero_idx]
            if not client.is_connected():
                logger.warning("[PHOTO] Clone #%s is offline", clone_idx)
                return
            ok = await set_profile_media(client, temp_path, name)
            logger.info(
                "[PHOTO] Profile photo for Clone #%s: %s",
                clone_idx,
                "success" if ok else "failed",
            )
            return

        watermark = pending["data"].get("watermark", False)
        for client, name in zip(
            automation.clone_clients,
            automation.clone_names,
        ):
            if not client.is_connected():
                results["skipped"] += 1
                continue

            upload_path = temp_path
            wm_path = None
            try:
                if watermark:
                    from watermark_engine import watermark_file
                    wm_path = watermark_file(temp_path, name, name)
                    if not wm_path:
                        results["failed"] += 1
                        continue
                    upload_path = wm_path

                if await set_profile_media(client, upload_path, name):
                    results["success"] += 1
                else:
                    results["failed"] += 1
            except Exception:
                results["failed"] += 1
                logger.error(
                    "[PHOTO] Failed for %s",
                    name,
                    exc_info=True,
                )
            finally:
                if wm_path and os.path.exists(wm_path):
                    os.unlink(wm_path)

        logger.info(
            "[PHOTO] Bulk complete: success=%s skipped=%s failed=%s",
            results["success"],
            results["skipped"],
            results["failed"],
        )
        if results["success"] or results["skipped"] == 0:
            from .normal_mode_store import save_clone_mode_field, save_mode_photo
            saved_path = save_mode_photo("clone", temp_path)
            save_clone_mode_field("watermark", bool(watermark))
            if saved_path:
                logger.info("[PHOTO] Clone Mode photo saved locally: %s", saved_path)
    except Exception as exc:
        logger.error("[PHOTO] Processing failed: %s", exc, exc_info=True)
    finally:
        clear_pending_input(
            pending.get(
                "_owner_id",
                message.from_user.id if message.from_user else None,
            )
        )
        if (
            message.from_user
            and pending.get("_owner_id") != message.from_user.id
        ):
            clear_pending_input(message.from_user.id)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        await _refresh_pending_target(pending, get_automation())


async def _handle_music_input(message: Message, pending: dict):
    """Save one clone's Normal Mode music file."""
    temp_path = None
    try:
        media = message.audio or message.document
        file = await message.bot.get_file(media.file_id)
        suffix = os.path.splitext(file.file_path or "")[1].lower()
        if suffix not in {".mp3", ".m4a", ".aac", ".ogg", ".wav"}:
            suffix = ".mp3"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
            temp_path = temp.name
        await message.bot.download_file(file.file_path, destination=temp_path)

        from .normal_mode_store import save_normal_music
        clone_idx = pending["data"].get("clone_idx")
        saved = save_normal_music(clone_idx, temp_path)
        logger.info("[MUSIC] Normal Mode Clone #%s saved: %s", clone_idx, saved)
    except Exception as exc:
        logger.error("[MUSIC] Normal Mode save failed: %s", exc, exc_info=True)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        clear_pending_input(
            pending.get(
                "_owner_id",
                message.from_user.id if message.from_user else None,
            )
        )
        await _refresh_pending_target(pending, get_automation())


async def _refresh_pending_target(pending, automation):
    """Refresh the original inline/private menu without sending a reply."""
    if automation is None:
        return
    target = pending.get("target") or {}
    if not target:
        return

    input_type = pending.get("type", "")
    data = pending.get("data") or {}
    if input_type == "session:toggle_number":
        from .menus.session_settings import build_session_settings
        text, keyboard = build_session_settings(
            automation,
            user_id=pending.get("_owner_id"),
            page=int(data.get("page", 1)),
        )
    elif input_type == "session:confirmation":
        from .menus.session_settings import build_session_confirmation
        text, keyboard = build_session_confirmation(
            int(data.get("number", 0)),
            bool(data.get("enabled")),
        )
    elif input_type == "session:activation_choice":
        from .menus.session_settings import build_activation_choice
        text, keyboard = build_activation_choice(
            int(data.get("number", 0)),
            int(data.get("new_number", 0)),
        )
    elif input_type.startswith("clone_mode:"):
        from .menus.clone_mode import build_clone_mode_menu
        if input_type.startswith("clone_mode:wm:"):
            from .menus.clone_mode import build_watermark_settings_menu
            text, keyboard = build_watermark_settings_menu(
                _watermark_menu_data(pending.get("_owner_id", 0))
            )
        else:
            text, keyboard = build_clone_mode_menu(automation)
    elif input_type.startswith("normal:"):
        from .menus.normal_mode import build_normal_clone_menu
        text, keyboard = build_normal_clone_menu(
            automation,
            int(data.get("clone_idx", 1)),
        )
    elif input_type.startswith("template:"):
        from .menus.template import build_template_menu
        from .menus.template import build_template_list_menu
        template_name = data.get("template_name")
        if input_type == "template:list" or not template_name:
            text, keyboard = build_template_list_menu()
        else:
            text, keyboard = build_template_menu(
                automation,
                template_name,
                int(data.get("clone_idx", 1)),
            )
    elif input_type.startswith("profile:"):
        from .menus.profile_menu import build_profile_clone_menu
        text, keyboard = build_profile_clone_menu(
            automation,
            int(data.get("clone_idx", 1)),
        )
    else:
        from .menus.profile_menu import build_profile_main_menu
        text, keyboard = build_profile_main_menu(automation)

    bot = get_bot_client()
    if bot is None:
        return
    try:
        normal_photo = None
        clone_photo = None
        if input_type.startswith("normal:"):
            from .menus.normal_mode import normal_photo_path
            normal_photo = normal_photo_path(int(data.get("clone_idx", 1)))
        elif input_type.startswith("template:"):
            from .menus.template import template_photo_path
            template_name = data.get("template_name")
            if template_name:
                normal_photo = template_photo_path(
                    template_name,
                    int(data.get("clone_idx", 1)),
                )
        elif input_type.startswith("clone_mode:"):
            from .normal_mode_store import CLONE_MODE_DIR, load_settings
            photo_name = load_settings()["clone"].get("photo", "")
            if photo_name:
                candidate = CLONE_MODE_DIR / photo_name
                if candidate.is_file():
                    clone_photo = candidate

        if target.get("inline_message_id"):
            await bot.edit_message_text(
                inline_message_id=target["inline_message_id"],
                text=text,
                reply_markup=keyboard,
            )
        elif normal_photo or clone_photo:
            photo_path = normal_photo or clone_photo
            edited = False
            try:
                await bot.edit_message_media(
                    chat_id=target["chat_id"],
                    message_id=target["message_id"],
                    media=InputMediaPhoto(
                        media=FSInputFile(str(photo_path)),
                        caption=text,
                    ),
                    reply_markup=keyboard,
                )
                edited = True
            except Exception:
                logger.debug("[MENU] Existing target media could not be replaced")
            if not edited:
                await bot.send_photo(
                    chat_id=target["chat_id"],
                    photo=FSInputFile(str(photo_path)),
                    caption=text,
                    reply_markup=keyboard,
                )
                try:
                    await bot.delete_message(
                        chat_id=target["chat_id"],
                        message_id=target["message_id"],
                    )
                except Exception:
                    logger.debug("[MENU] Could not delete old photo menu")
        else:
            await bot.edit_message_text(
                chat_id=target["chat_id"],
                message_id=target["message_id"],
                text=text,
                reply_markup=keyboard,
            )
    except Exception:
        logger.debug("[MENU] Could not refresh target message", exc_info=True)

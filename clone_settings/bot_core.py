"""
Clone Manager Bot core, implemented with aiogram 3.30.0.

The manager bot runs as an asyncio task on the application's main loop.
The clone accounts continue to be controlled by the existing Telethon
user clients on that same loop.
"""

__TCM_FILE_HASH__ = "7604812395"


import asyncio
import json
import logging
from urllib.request import urlopen
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from telethon import events

from config import CLONE_MANAGER_BOT_TOKEN

logger = logging.getLogger("CloneManager")

_bot_client: Optional[Bot] = None
_dispatcher: Optional[Dispatcher] = None
_automation = None
_admin_user_id: Optional[int] = None
_bot_loop: Optional[asyncio.AbstractEventLoop] = None
_bot_task: Optional[asyncio.Task] = None
_bot_username: Optional[str] = None
_bridge_client = None


def _get_bot_username_sync(token: str) -> str:
    """Resolve the bot username without blocking the application event loop."""
    with urlopen(
        f"https://api.telegram.org/bot{token}/getMe", timeout=10
    ) as response:
        payload = json.load(response)
    if not payload.get("ok") or not payload.get("result", {}).get("username"):
        raise RuntimeError("Telegram did not return the bot username")
    return payload["result"]["username"]


def _log_bot_task_result(task):
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "[CLONE-MGR] Background bot task stopped: %s",
            error,
            exc_info=error,
        )


def get_bot_client() -> Optional[Bot]:
    return _bot_client


def get_automation():
    return _automation


def get_admin_user_id() -> Optional[int]:
    return _admin_user_id


def is_temp_main_user(user_id: int) -> bool:
    """Return whether a user currently has Temporary Main access."""
    try:
        from temp_main_manager import temp_main
        return temp_main.is_temp_main(user_id)
    except Exception:
        return False


def get_bot_loop() -> Optional[asyncio.AbstractEventLoop]:
    return _bot_loop


def get_bot_username() -> Optional[str]:
    return _bot_username


async def _finish_update_restart(automation):
    try:
        from updater import consume_restart_notice

        notice = consume_restart_notice()
    except Exception:
        logger.debug("[CLONE-MGR] No pending update restart notice", exc_info=True)
        return
    if not notice or _bot_client is None:
        return
    chat_id = notice.get("chat_id")
    message_id = notice.get("message_id")
    if chat_id and message_id:
        try:
            await _bot_client.delete_message(chat_id, message_id)
        except Exception:
            logger.debug(
                "[CLONE-MGR] Could not delete update restart notice",
                exc_info=True,
            )
    from .menus.main_menu import build_main_menu

    text, keyboard = build_main_menu(automation, user_id=chat_id)
    try:
        await _bot_client.send_message(
            chat_id,
            text,
            reply_markup=keyboard,
        )
    except Exception:
        logger.error(
            "[CLONE-MGR] Could not send post-update main menu",
            exc_info=True,
        )


def is_admin(user_id: int) -> bool:
    if user_id is None:
        return False
    try:
        from temp_main_manager import temp_main
        if temp_main.active and user_id != temp_main.original_admin_id:
            return False
    except Exception:
        pass
    if _admin_user_id is not None and user_id == _admin_user_id:
        return True
    try:
        from commander_manager import identity_ids
        return user_id in identity_ids().values()
    except Exception:
        return False


def can_operate(user_id: int) -> bool:
    """Return whether this commander currently owns bot operations."""
    if user_id is None:
        return False
    try:
        from temp_main_manager import temp_main
        if temp_main.active and user_id != temp_main.original_admin_id:
            return False
    except Exception:
        pass
    try:
        from commander_manager import is_active
        return is_active(user_id)
    except Exception:
        return _admin_user_id is not None and user_id == _admin_user_id


def can_access_normal_menu(user_id: int) -> bool:
    try:
        from temp_main_manager import temp_main
        if temp_main.active and user_id != temp_main.original_admin_id:
            return False
    except Exception:
        pass
    try:
        from .normal_access import has_access
        return has_access(user_id)
    except Exception:
        return False


async def _start_bot(automation):
    global _bot_client, _dispatcher, _automation, _admin_user_id
    global _bot_loop, _bot_username

    _automation = automation
    _bot_loop = asyncio.get_running_loop()

    if not CLONE_MANAGER_BOT_TOKEN or CLONE_MANAGER_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.warning("[CLONE-MGR] No bot token configured — skipping")
        return

    
    
    
    _admin_user_id = None
    main_client = getattr(automation, "main_client", None)
    try:
        if main_client is not None and main_client.is_connected():
            main_user = await main_client.get_me()
            _admin_user_id = getattr(main_user, "id", None)
            automation._admin_user_id = _admin_user_id
            from commander_manager import (
                active_name,
                register_identity,
            )
            register_identity(active_name() or "main_commander", _admin_user_id)
    except Exception as exc:
        logger.error("[CLONE-MGR] Cannot resolve Main admin: %s", exc)
    if _admin_user_id is None:
        logger.warning(
            "[CLONE-MGR] Main admin ID is unavailable; bot will deny all users"
        )
    else:
        logger.info(
            "[CLONE-MGR] Main account registered as admin: ID %s",
            _admin_user_id,
        )

    _bot_client = Bot(
        token=CLONE_MANAGER_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=None),
    )
    from power_monitor import start_power_monitor
    await start_power_monitor(_bot_client, _admin_user_id)
    _dispatcher = Dispatcher()
    try:
        _bot_username = await asyncio.to_thread(
            _get_bot_username_sync, CLONE_MANAGER_BOT_TOKEN
        )
    except Exception as exc:
        logger.error("[CLONE-MGR] Cannot resolve bot username: %s", exc)
        await _bot_client.session.close()
        return
    logger.info("[CLONE-MGR] Bot identity resolved: @%s", _bot_username)

    from .inline_handler import register_inline_handlers
    from .callback_handler import register_callback_handlers

    register_inline_handlers(_dispatcher)

    command_router = Router(name="clone_manager_commands")

    @command_router.message(Command("start"))
    async def _on_start(message):
        if is_temp_main_user(message.from_user.id if message.from_user else None):
            await message.answer("You are Temporary Main.")
            return
        if can_access_normal_menu(
            message.from_user.id if message.from_user else None
        ):
            from .menus.normal_mode import build_normal_main_menu
            text, keyboard = build_normal_main_menu(
                automation,
                delegated=True,
            )
            await message.answer(text, reply_markup=keyboard)
            return
        if not can_operate(message.from_user.id if message.from_user else None):
            return

        clone_count = len(automation.clone_clients)
        online = sum(1 for client in automation.clone_clients if client.is_connected())
        await message.answer(
            "🤖 **Clone Manager**\n\n"
            "Welcome, admin!\n\n"
            f"📊 **Current Status:**\n"
            f"  • Clones: {online}/{clone_count} online\n"
            f"  • Mirror: {'ON ✅' if automation.mirror_mode else 'OFF ❌'}\n"
            f"  • Loops: {len(automation.loops.active)}\n\n"
            "Use `/menu` or type the bot username inline to open settings."
        )

    @command_router.message(Command("go"))
    async def _on_temp_main_go(message):
        
        
        user_id = message.from_user.id if message.from_user else None
        if not is_temp_main_user(user_id):
            return
        from handlers.go_handler import parse_go_request, start_temp_main_go
        reply_text = (
            message.reply_to_message.text
            if message.reply_to_message is not None else None
        )
        request = parse_go_request(message.text, reply_text)
        if not request:
            return
        target, target_text = request
        await start_temp_main_go(
            automation,
            target,
            target_text,
            [user_id, get_admin_user_id()],
        )

    @command_router.message(Command("add_session"))
    @command_router.message(Command("new_session"))
    async def _on_add_session(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from .utils.keyboards import make_inline_keyboard
        await message.answer(
            "➕ Choose the type of session to create:",
            reply_markup=make_inline_keyboard([
                [
                    ("👤 Main", "session:create:main"),
                    ("🧩 Clone", "session:create:clone"),
                ],
                [("❌ Cancel", "session:create:cancel")],
            ]),
        )

    @command_router.message(Command("menu"))
    async def _on_menu(message):
        if can_access_normal_menu(
            message.from_user.id if message.from_user else None
        ):
            from .menus.normal_mode import build_normal_main_menu
            text, keyboard = build_normal_main_menu(
                automation,
                delegated=True,
            )
            await message.answer(text, reply_markup=keyboard)
            return
        if not is_admin(message.from_user.id if message.from_user else None):
            return
        from .menus.main_menu import build_main_menu
        text, keyboard = build_main_menu(
            automation,
            user_id=message.from_user.id if message.from_user else None,
        )
        await message.answer(text, reply_markup=keyboard)

    @command_router.message(Command("status"))
    async def _on_status(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from .menus.status_menu import build_status_text
        await message.answer(await build_status_text(automation))

    @command_router.message(Command("clones"))
    async def _on_clones(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from .menus.clone_list import build_clone_list
        text, keyboard = build_clone_list(automation)
        await message.answer(text, reply_markup=keyboard)

    @command_router.message(Command("telethon_status"))
    async def _on_telethon_status(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from internal_api import telethon_status
        result = await asyncio.to_thread(telethon_status)
        await message.answer(
            "Telethon status:\n"
            f"State: {result.get('state', 'unknown')}\n"
            f"Main: {'online' if result.get('main_online') else 'offline'}\n"
            f"Clones: {result.get('clones_online', 0)}/"
            f"{result.get('clones_total', 0)}"
        )

    @command_router.message(Command("telethon_start"))
    async def _on_telethon_start(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from internal_api import telethon_start
        result = await asyncio.to_thread(telethon_start)
        await message.answer(result.get("message", "Telethon start requested"))

    @command_router.message(Command("telethon_stop"))
    async def _on_telethon_stop(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from internal_api import telethon_stop
        result = await asyncio.to_thread(telethon_stop)
        await message.answer(result.get("message", "Telethon stop requested"))

    @command_router.message(Command("telethon_restart"))
    async def _on_telethon_restart(message):
        if not can_operate(message.from_user.id if message.from_user else None):
            return
        from internal_api import telethon_restart
        result = await asyncio.to_thread(telethon_restart)
        await message.answer(result.get("message", "Telethon restart requested"))

    _dispatcher.include_router(command_router)
    
    
    
    register_callback_handlers(_dispatcher)
    _register_main_reply_bridge(automation)
    await _finish_update_restart(automation)

    logger.info("[CLONE-MGR] Starting aiogram polling")
    try:
        await _dispatcher.start_polling(
            _bot_client,
            allowed_updates=_dispatcher.resolve_used_update_types(),
            handle_signals=False,
        )
    finally:
        await _bot_client.session.close()
        logger.info("[CLONE-MGR] Bot polling stopped")


async def _stop_bot_async():
    global _bot_client, _dispatcher, _automation, _admin_user_id
    global _bot_loop, _bot_username, _bridge_client
    try:
        from power_monitor import stop_power_monitor
        await stop_power_monitor()
    except Exception:
        logger.debug("[POWER] Monitor stop failed", exc_info=True)
    if _dispatcher is not None:
        try:
            await _dispatcher.stop_polling()
        except RuntimeError:
            pass
    if _bot_client is not None:
        try:
            await _bot_client.session.close()
        except Exception:
            pass
    _bot_client = None
    _dispatcher = None
    _automation = None
    _admin_user_id = None
    _bot_loop = None
    _bot_username = None
    _bridge_client = None


async def stop_bot():
    """Stop the aiogram bot on the same loop as the Telethon clients."""
    global _bot_task
    if _bot_task is None:
        return

    try:
        await _stop_bot_async()
        if not _bot_task.done():
            await _bot_task
    except Exception:
        logger.debug("[CLONE-MGR] Bot stop failed", exc_info=True)
    finally:
        _bot_task = None


def run_clone_manager_bot(automation):
    """Schedule the manager bot on the current application event loop."""
    global _bot_task

    if not CLONE_MANAGER_BOT_TOKEN or CLONE_MANAGER_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        logger.info("[CLONE-MGR] No bot token — Clone Manager disabled")
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("[CLONE-MGR] Cannot start without an active event loop")
        return

    if _bot_task is not None and not _bot_task.done():
        logger.info("[CLONE-MGR] Bot is already running")
        return

    _bot_task = loop.create_task(_start_bot(automation))
    _bot_task.add_done_callback(_log_bot_task_result)
    logger.info("[CLONE-MGR] Polling task scheduled")


def _register_main_reply_bridge(automation):
    """Forward Main replies from Saved Messages into the bot private chat."""
    global _bridge_client
    client = getattr(automation, "main_client", None)
    if client is None or not _bot_username or _bridge_client is client:
        return

    main_user_id = (
        getattr(getattr(client, "_self_user", None), "id", None)
        or getattr(client, "_self_id", None)
    )
    if main_user_id is None:
        logger.warning("[CLONE-MGR] Could not resolve Main user ID for reply bridge")
        return

    from .callback_handler import get_pending_for_users

    
    
    
    @client.on(events.NewMessage(outgoing=True))
    async def _on_main_reply(event):
        message = event.message
        if not message.reply_to_msg_id:
            return

        try:
            replied = await event.get_reply_message()
        except Exception:
            return
        if replied is None:
            return

        _, pending = get_pending_for_users(
            [main_user_id, getattr(automation, "_admin_user_id", None)]
        )
        if pending is None:
            return

        bot_entity = None
        try:
            bot_entity = await client.get_entity(_bot_username)
        except Exception:
            pass
        bot_id = getattr(bot_entity, "id", None)

        
        
        
        if bot_id is not None and event.chat_id == bot_id:
            return

        replied_sender_id = getattr(replied, "sender_id", None)
        replied_username = getattr(
            getattr(replied, "sender", None), "username", None
        )
        is_bot_reply = (
            (bot_id is not None and replied_sender_id == bot_id)
            or (
                replied_username
                and _bot_username
                and replied_username.lower() == _bot_username.lower()
            )
        )

        
        
        
        
        
        saved_message_input = (
            event.chat_id == main_user_id
            and pending.get("type") in {
                "clone_mode:photo",
                "clone_mode:name",
                "clone_mode:bio",
                "normal:photo",
                "normal:name",
                "normal:bio",
                "profile:photo",
                "profile:name",
                "profile:bio",
                "profile:username",
            }
        )
        if not is_bot_reply and not saved_message_input:
            return

        if not message.raw_text and not message.media:
            return

        try:
            pending["bridged"] = True
            if message.media:
                await client.send_file(
                    _bot_username,
                    message.media,
                    caption=message.raw_text or None,
                )
            else:
                await client.send_message(_bot_username, message.raw_text)

            await client.delete_messages(main_user_id, message.id)
            logger.info("[CLONE-MGR] Main reply bridged to bot and deleted")
        except Exception:
            pending.pop("bridged", None)
            logger.error("[CLONE-MGR] Failed to bridge Main reply", exc_info=True)

    _bridge_client = client
    logger.info("[CLONE-MGR] Main Saved Messages bridge registered")

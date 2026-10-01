
"""
Telegram bot runner — direct execution with state persistence.
Runs the Telegram bot with Telethon + starts:
  - Public web dashboard (port 1500) — limited view + music player
  - Internal admin API (localhost:1600) — full control via CLI client
  - Clone Manager bot — inline settings bot via BotFather

Features:
  - Persistent state (loops/sequential sends/hunt-clicks resume after restart)
  - Invalid sessions remain available for inspection
  - Hot reload support
  - Temporary Main delegation
  - Graceful shutdown
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)






def _load_dotenv():
    """Load .env file into os.environ (no external dependency)."""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_dotenv()






import asyncio
import logging
import glob
import re
import subprocess
import threading
from typing import List, Optional

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    SessionRevokedError,
    SessionExpiredError,
    UnauthorizedError,
)

from config import (
    API_ID, API_HASH,
    SESSIONS_DIR,
    MAIN_SESSION_NAME,
    BRIDGE_GROUP,
    BUTTON_CLICK_TIMEOUT,
    AUTO_DELETE_DELAY,
)
from logger_setup import setup_logging, print_banner, print_status_box
from colors import C
from loop_manager import LoopRegistry
from handlers import register_all_handlers
from web import (
    set_automation as set_web_automation,
    run_web_server,
    setup_web_logging,
)
from admin_api import (
    set_automation as set_api_automation,
    run_admin_api,
)
from temp_main_manager import temp_main

setup_logging()
logger = logging.getLogger("TG-Auto")






DEAD_SESSION_ERRORS = (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    SessionRevokedError,
    SessionExpiredError,
    UnauthorizedError,
)


def _delete_failed_clone_files(session_path):
    """Delete a clone session and its metadata after a confirmed fatal error."""
    session_file = (
        session_path
        if session_path.endswith(".session")
        else session_path + ".session"
    )
    for path in (
        session_file,
        session_file + "-journal",
        os.path.splitext(session_file)[0] + ".json",
    ):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.error("[SESSION] Could not delete %s: %s", path, exc)


async def _notify_failed_clone(main_client, clone_name, reason):
    """Notify the administrator after a confirmed fatal clone error."""
    text = (
        f"⚠️ Clone session {clone_name} is no longer valid "
        f"({reason}) and was removed automatically.\n\n"
        "Open 🔧Session Settings⚙ to review and recreate the session."
    )
    try:
        admin_id = getattr(main_client, "_self_id", None)
        if main_client is not None and admin_id is not None:
            await main_client.send_message(admin_id, text)
            return
    except Exception:
        logger.debug("[SESSION] Main notification failed", exc_info=True)
    try:
        from clone_settings.bot_core import get_bot_client, get_admin_user_id
        bot = get_bot_client()
        admin_id = get_admin_user_id()
        if bot is not None and admin_id is not None:
            await bot.send_message(admin_id, text)
    except Exception:
        logger.debug("[SESSION] Bot notification failed", exc_info=True)






def validate_config():
    if BRIDGE_GROUP is not None:
        if not isinstance(BRIDGE_GROUP, int):
            raise TypeError("BRIDGE_GROUP must be int or None")
        if BRIDGE_GROUP >= 0:
            raise ValueError("BRIDGE_GROUP must be negative")
        logger.info(f"[CONFIG] BRIDGE_GROUP={BRIDGE_GROUP} ✓")
    else:
        logger.info("[CONFIG] BRIDGE_GROUP=None")

    if not isinstance(BUTTON_CLICK_TIMEOUT, int) or BUTTON_CLICK_TIMEOUT < 1:
        raise ValueError("BUTTON_CLICK_TIMEOUT must be positive int")
    logger.info(f"[CONFIG] BUTTON_CLICK_TIMEOUT={BUTTON_CLICK_TIMEOUT}s ✓")
    logger.info(f"[CONFIG] AUTO_DELETE_DELAY={AUTO_DELETE_DELAY}s ✓")






def safe_discover_sessions():
    """Discover sessions safely — never raises."""
    from commander_manager import active_name, is_commander_name

    if not os.path.isdir(SESSIONS_DIR):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        logger.warning("[DISCOVERY] Created empty sessions directory")
        return None, []

    files = glob.glob(os.path.join(SESSIONS_DIR, "*.session"))
    if not files:
        logger.warning("[DISCOVERY] No .session files found")
        return None, []

    main_path = None
    clone_paths = []
    active_commander = active_name()

    for sf in files:
        base = os.path.splitext(os.path.basename(sf))[0]
        if base.startswith("_"):
            continue
        path = os.path.join(SESSIONS_DIR, base)
        if is_commander_name(base):
            if base.lower() == (active_commander or "").lower():
                main_path = path
            continue
        if base.lower() == MAIN_SESSION_NAME.lower():
            main_path = path
        else:
            clone_paths.append(path)

    def _key(p):
        parts = re.split(r'(\d+)', os.path.basename(p))
        return [int(x) if x.isdigit() else x.lower() for x in parts]

    clone_paths.sort(key=_key)

    if main_path is None:
        logger.warning(
            f"[DISCOVERY] Main session ({MAIN_SESSION_NAME}.session) not found"
        )
    else:
        logger.info(f"[DISCOVERY] Main → {os.path.basename(main_path)}")

    for i, cp in enumerate(clone_paths):
        logger.info(f"[DISCOVERY] Clone #{i + 1} → {os.path.basename(cp)}")

    logger.info(
        f"[DISCOVERY] Found {'1' if main_path else '0'} Main + "
        f"{len(clone_paths)} Clone(s)"
    )
    return main_path, clone_paths






class TelegramAutomation:

    def __init__(self):
        self.main_client = None
        self.commander_clients = {}
        self.clone_clients = []
        self.clone_names = []
        self._admin_user_id = None
        self.mirror_mode = False
        try:
            from clone_settings.normal_mode_store import (
                load_normal_mode_enabled,
            )
            self.normal_mode = load_normal_mode_enabled()
        except Exception:
            self.normal_mode = False
        self.loops = LoopRegistry()

        
        self.bot_running = False
        self.startup_error = None
        self.main_available = False

        
        self._reload_requested = False
        self._reload_lock = asyncio.Lock()
        self._reload_flag_lock = threading.Lock()
        self._event_loop = None
        self._repairing = False
        self._repair_main_task = None
        self._source_restart_requested = False
        self._telethon_state_lock = asyncio.Lock()
        self._telethon_task = None
        self._telethon_state = "stopped"

        
        self._shutdown_requested = False

        
        self._temp_main_active = False
        self._temp_main_user_id = None

    
    
    

    def is_admin(self, event):
        if temp_main.active:
            if event.sender_id == temp_main.temp_user_id:
                return True
            if event.sender_id == self._admin_user_id:
                raw = (event.raw_text or "").strip().lower()
                if raw.startswith("untmp main") or raw.startswith("temp main"):
                    return True
                return False
            return False

        return (
            self._admin_user_id is not None
            and event.sender_id == self._admin_user_id
        )

    
    
    

    def _set_reload_requested(self) -> bool:
        with self._reload_flag_lock:
            if self._reload_requested:
                return False
            self._reload_requested = True
            return True

    def _clear_reload_requested(self):
        with self._reload_flag_lock:
            self._reload_requested = False

    def request_reload(self):
        if not self._set_reload_requested():
            return {"success": False, "message": "Reload already in progress"}
        logger.info("[RELOAD] Reload requested")
        return {"success": True, "message": "Reload triggered. Wait 5-10s."}

    def telethon_status(self):
        connected = bool(
            self.main_client is not None
            and self.main_client.is_connected()
        )
        return {
            "success": True,
            "service": "telethon",
            "state": "running" if connected else self._telethon_state,
            "main_online": connected,
            "clones_online": sum(
                1 for client in self.clone_clients
                if client.is_connected()
            ),
            "clones_total": len(self.clone_clients),
        }

    def start_telethon_sync(self, timeout=180):
        if self._event_loop is None or self._event_loop.is_closed():
            return {"success": False, "message": "Event loop unavailable"}
        future = asyncio.run_coroutine_threadsafe(
            self._start_telethon(),
            self._event_loop,
        )
        try:
            return future.result(timeout=timeout)
        except Exception as exc:
            future.cancel()
            return {"success": False, "message": str(exc)}

    def stop_telethon_sync(self, timeout=180):
        if self._event_loop is None or self._event_loop.is_closed():
            return {"success": False, "message": "Event loop unavailable"}
        future = asyncio.run_coroutine_threadsafe(
            self._stop_telethon(),
            self._event_loop,
        )
        try:
            return future.result(timeout=timeout)
        except Exception as exc:
            future.cancel()
            return {"success": False, "message": str(exc)}

    def restart_telethon_sync(self, timeout=300):
        if self._event_loop is None or self._event_loop.is_closed():
            return {"success": False, "message": "Event loop unavailable"}
        future = asyncio.run_coroutine_threadsafe(
            self._restart_telethon(),
            self._event_loop,
        )
        try:
            return future.result(timeout=timeout)
        except Exception as exc:
            future.cancel()
            return {"success": False, "message": str(exc)}

    async def _stop_telethon(self):
        async with self._telethon_state_lock:
            if self._telethon_state == "stopping":
                return {"success": False, "message": "Telethon stop already running"}
            self._telethon_state = "stopping"
            try:
                await self.loops.cancel_all()
            except Exception:
                pass
            for client in list(self.clone_clients):
                try:
                    await client.disconnect()
                except Exception:
                    pass
            if self.main_client is not None:
                try:
                    await self.main_client.disconnect()
                except Exception:
                    pass
            for client in self.commander_clients.values():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self.commander_clients = {}
            self.main_client = None
            self.clone_clients = []
            self.clone_names = []
            self._admin_user_id = None
            self.main_available = False
            self.bot_running = False
            self._telethon_state = "stopped"
            return {"success": True, "message": "Telethon stopped"}

    async def _start_telethon(self):
        async with self._telethon_state_lock:
            if (
                self.main_client is not None
                and self.main_client.is_connected()
            ):
                self._telethon_state = "running"
                return self.telethon_status()
            self._telethon_state = "starting"
            self.clone_clients = []
            self.clone_names = []
            success = await self.initialize()
            if not success or self.main_client is None:
                self._telethon_state = "failed"
                return {
                    "success": False,
                    "message": self.startup_error or "Telethon start failed",
                }
            register_all_handlers(self)
            self.bot_running = True
            self._telethon_state = "running"
            if (
                self._telethon_task is None
                or self._telethon_task.done()
            ):
                self._telethon_task = asyncio.create_task(
                    self._run_main_forever()
                )
            return {
                "success": True,
                "message": "Telethon started",
                **self.telethon_status(),
            }

    async def _restart_telethon(self):
        await self._stop_telethon()
        await asyncio.sleep(1)
        return await self._start_telethon()

    def repair_sessions_sync(self, timeout=300):
        """Run session repair on the bot's own asyncio loop."""
        if self._event_loop is None or self._event_loop.is_closed():
            return {
                "success": False,
                "message": "Bot event loop is not available",
            }
        future = asyncio.run_coroutine_threadsafe(
            self._repair_sessions(),
            self._event_loop,
        )
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            future.cancel()
            return {
                "success": False,
                "message": f"Repair timed out after {timeout}s",
            }
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    async def repair_clone_sessions_from_manager(self, progress_callback=None):
        """Repair clone files after closing their active Telethon clients."""
        async with self._reload_lock:
            if self._repairing:
                return {"success": False, "message": "Repair already running"}
            self._repairing = True
            try:
                if self.main_client is not None:
                    try:
                        await self.main_client.disconnect()
                    except Exception as exc:
                        logger.warning(
                            "[REPAIR] Could not disconnect Main client: %s",
                            exc,
                        )
                    self.main_client = None
                for client in list(self.clone_clients):
                    try:
                        await client.disconnect()
                    except Exception as exc:
                        logger.warning(
                            "[REPAIR] Could not disconnect clone client: %s",
                            exc,
                        )
                for client in self.commander_clients.values():
                    try:
                        await client.disconnect()
                    except Exception as exc:
                        logger.warning(
                            "[REPAIR] Could not disconnect commander client: %s",
                            exc,
                        )
                self.commander_clients = {}
                self.clone_clients = []
                self.clone_names = []

                from session_manager import repair_clone_sessions
                report = await repair_clone_sessions(
                    progress_callback=progress_callback
                )
                self.request_reload()
                return report
            finally:
                self._repairing = False

    async def _repair_sessions(self):
        async with self._reload_lock:
            if self._repairing:
                return {"success": False, "message": "Repair already running"}
            self._repairing = True
            self.bot_running = False
            logger.info("[REPAIR] Stopping bot and checking sessions")
            try:
                try:
                    from clone_settings.bot_core import stop_bot
                    await stop_bot()
                except Exception:
                    pass

                try:
                    await self.loops.cancel_all()
                except Exception as exc:
                    logger.warning("[REPAIR] Could not stop loops: %s", exc)

                for client in self.clone_clients:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                for client in self.commander_clients.values():
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                self.commander_clients = {}
                if self.main_client:
                    try:
                        await self.main_client.disconnect()
                    except Exception:
                        pass

                self.main_client = None
                self.clone_clients = []
                self.clone_names = []
                self._admin_user_id = None
                self.mirror_mode = False

                from session_manager import repair_clone_sessions
                report = await repair_clone_sessions()

                success = await self.initialize()
                if not success or self.main_client is None:
                    report["success"] = False
                    report["message"] = "Sessions repaired, but bot restart failed"
                    return report

                
                
                
                if not self.main_client.is_connected():
                    await self.main_client.connect()
                if not self.main_client.is_connected():
                    raise ConnectionError("Main client did not reconnect")

                connected_clones = 0
                for index, client in enumerate(self.clone_clients):
                    if not client.is_connected():
                        await client.connect()
                    if client.is_connected():
                        connected_clones += 1
                    else:
                        logger.warning(
                            "[REPAIR] Clone %s is still disconnected",
                            self.clone_names[index],
                        )

                self.main_available = True
                register_all_handlers(self)
                logger.info(
                    "[REPAIR] Telethon clients online: main + %s/%s clones",
                    connected_clones,
                    len(self.clone_clients),
                )
                asyncio.create_task(self._resume_after_startup())
                self._start_clone_manager()
                self.bot_running = True
                if (
                    self._repair_main_task is None
                    or self._repair_main_task.done()
                ):
                    self._repair_main_task = asyncio.create_task(
                        self._run_main_forever()
                    )
                report["message"] = "Sessions repaired and bot restarted"
                report["online_clones"] = connected_clones
                return report
            finally:
                self._repairing = False

    async def _handle_unexpected_main_disconnect(self, reason: str):
        self.bot_running = False
        if self._repairing:
            logger.info("[MAIN] Disconnected for session repair")
            return
        if self._shutdown_requested:
            logger.info(f"[MAIN] Disconnected during shutdown: {reason}")
            return
        if self._set_reload_requested():
            logger.warning(
                f"[MAIN] Unexpected disconnect — scheduling reload: {reason}"
            )
        else:
            logger.warning(
                f"[MAIN] Unexpected disconnect while reload pending: {reason}"
            )

    async def _do_reload(self):
        async with self._reload_lock:
            logger.info("=" * 60)
            logger.info("[RELOAD] Starting bot reload…")
            logger.info("=" * 60)

            if temp_main.active:
                temp_main.deactivate()
                self._temp_main_active = False
                self._temp_main_user_id = None

            try:
                await self.loops.cancel_all()
            except Exception as e:
                logger.error(f"[RELOAD] Cancel loops failed: {e}")

            for client in self.clone_clients:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            for client in self.commander_clients.values():
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self.commander_clients = {}

            if self.main_client:
                try:
                    await self.main_client.disconnect()
                except Exception:
                    pass

            self.main_client = None
            self.clone_clients = []
            self.clone_names = []
            self._admin_user_id = None
            self.mirror_mode = False
            self.bot_running = False
            self.main_available = False
            self.startup_error = None

            try:
                success = await self.initialize()

                if success and self.main_client is not None:
                    try:
                        register_all_handlers(self)
                        logger.info("[RELOAD] ✓ Handlers re-registered")
                        self.bot_running = True

                        asyncio.create_task(self._resume_after_startup())
                        asyncio.create_task(self._run_main_forever())

                        logger.info("=" * 60)
                        logger.info("[RELOAD] ✓ Bot reloaded successfully")
                        logger.info("=" * 60)
                    except Exception as e:
                        logger.error(
                            f"[RELOAD] Handler failed: {e}", exc_info=True,
                        )
                        self.startup_error = str(e)
                else:
                    logger.error("[RELOAD] ✗ Reload failed")
            finally:
                self._clear_reload_requested()

    async def _run_main_forever(self):
        try:
            if self.main_client:
                await self.main_client.run_until_disconnected()
                await self._handle_unexpected_main_disconnect(
                    "run_until_disconnected returned"
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[MAIN] Disconnected with error: {e}")
            await self._handle_unexpected_main_disconnect(
                f"{type(e).__name__}: {e}"
            )

    async def _reload_watcher(self):
        while True:
            try:
                if self._reload_requested and not self._reload_lock.locked():
                    await self._do_reload()
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[RELOAD-WATCHER] {e}")
                await asyncio.sleep(5)

    
    
    

    async def initialize(self):
        
        
        try:
            from session_manager import REPAIR_STATE_FILE, repair_clone_sessions
            if os.path.isfile(REPAIR_STATE_FILE):
                logger.warning("[REPAIR] Resuming interrupted Session Repair")
                await repair_clone_sessions()
        except Exception as exc:
            self.startup_error = f"Interrupted session repair failed: {exc}"
            logger.error("[REPAIR] Resume failed: %s", exc, exc_info=True)
            return False

        main_path, clone_paths = safe_discover_sessions()

        if main_path is None:
            self.startup_error = (
                "No Main session found. Create one via CLI: "
                "python cli_client.py"
            )
            logger.error(f"[MAIN] ✗ {self.startup_error}")
            return False

        try:
            self.main_client = TelegramClient(main_path, API_ID, API_HASH)
            await self.main_client.connect()

            if not await self.main_client.is_user_authorized():
                await self._safe_disconnect_main()
                fallback_path = os.path.join(SESSIONS_DIR, "Main.session")
                if (
                    os.path.basename(main_path).lower()
                    == "main_commander.session"
                    and os.path.isfile(fallback_path)
                    and os.path.abspath(fallback_path) != os.path.abspath(main_path)
                ):
                    logger.warning(
                        "[MAIN] main_commander.session is not authorized; "
                        "checking legacy Main.session"
                    )
                    self.main_client = TelegramClient(
                        fallback_path,
                        API_ID,
                        API_HASH,
                    )
                    await self.main_client.connect()
                    if await self.main_client.is_user_authorized():
                        main_path = fallback_path
                        clone_paths = [
                            path for path in clone_paths
                            if os.path.abspath(path) != os.path.abspath(fallback_path)
                        ]
                        logger.warning(
                            "[MAIN] Using authorized legacy Main.session "
                            "as the active commander fallback"
                        )
                    else:
                        await self._safe_disconnect_main()
                        self.startup_error = (
                            "main_commander.session and legacy Main.session "
                            "are not authorized. Recreate the commander session."
                        )
                        logger.error(f"[MAIN] ✗ {self.startup_error}")
                        return False
                else:
                    self.startup_error = (
                        "Main session not authorized. Recreate via CLI."
                    )
                    logger.error(f"[MAIN] ✗ {self.startup_error}")
                    return False

            main_me = await self.main_client.get_me()
            logger.info(
                f"[MAIN] ✓ Connected: {main_me.first_name} "
                f"(@{main_me.username}, ID={main_me.id})"
            )
            self.main_available = True

        except DEAD_SESSION_ERRORS as e:
            self.startup_error = (
                f"Main session DEAD ({type(e).__name__})."
            )
            logger.error(f"[MAIN] ✗ {self.startup_error}")
            await self._safe_disconnect_main()
            return False

        except Exception as e:
            self.startup_error = (
                f"Main connection failed: {type(e).__name__}: {e}"
            )
            logger.error(f"[MAIN] ✗ {self.startup_error}")
            await self._safe_disconnect_main()
            return False

        await self._resolve_admin_id()
        await self._load_commander_identities()

        
        try:
            from session_manager import (
                auto_join_bridge,
                bridge_join_was_completed,
                get_bridge_invite_link,
            )
            invite_link = get_bridge_invite_link()
            bridge_enabled = invite_link and BRIDGE_GROUP is not None
        except Exception as e:
            logger.error(f"[BRIDGE-JOIN] Setup error: {e}")
            invite_link = None
            bridge_enabled = False

        if bridge_enabled and not bridge_join_was_completed("Main"):
            logger.info("[BRIDGE-JOIN] [Main] Joining bridge…")
            try:
                await auto_join_bridge(self.main_client, "Main")
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"[BRIDGE-JOIN] [Main] Failed: {e}")
        elif bridge_enabled:
            logger.debug("[BRIDGE-JOIN] [Main] Already processed for this invite")

        
        for cp in clone_paths:
            name = os.path.basename(cp)
            client = None
            try:
                client = TelegramClient(cp, API_ID, API_HASH)
                await client.connect()

                if not await client.is_user_authorized():
                    logger.warning(f"[CLONE] ✗ {name}: not authorized")
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    _delete_failed_clone_files(cp)
                    await _notify_failed_clone(
                        self.main_client, name, "not authorized"
                    )
                    continue

                me = await client.get_me()
                self.clone_clients.append(client)
                self.clone_names.append(name)
                logger.info(
                    f"[CLONE] ✓ {name}: {me.first_name} (@{me.username})"
                )

                if bridge_enabled and not bridge_join_was_completed(name):
                    logger.info(f"[BRIDGE-JOIN] [{name}] Joining…")
                    try:
                        from session_manager import auto_join_bridge
                        await auto_join_bridge(client, name)
                        await asyncio.sleep(2)
                    except Exception as e:
                        logger.error(
                            f"[BRIDGE-JOIN] [{name}] Failed: {e}"
                        )
                elif bridge_enabled:
                    logger.debug(
                        f"[BRIDGE-JOIN] [{name}] Already processed for this invite"
                    )

            except DEAD_SESSION_ERRORS as e:
                logger.error(
                    f"[CLONE] ✗ {name} DEAD ({type(e).__name__})"
                )
                if client:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                _delete_failed_clone_files(cp)
                await _notify_failed_clone(
                    self.main_client, name, type(e).__name__
                )
            except Exception as e:
                logger.error(
                    f"[CLONE] ✗ Failed '{name}': "
                    f"{type(e).__name__}: {e}"
                )
                if client:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

        try:
            from privacy_engine import (
                apply_clone_defaults,
                apply_saved_main_photo_targets,
            )
            privacy_results = await apply_clone_defaults(self)
            logger.info(
                "[PRIVACY] Clone defaults applied: %s success, %s failed",
                privacy_results["success"],
                privacy_results["failed"],
            )
            main_privacy = await apply_saved_main_photo_targets(self)
            logger.info(
                "[PRIVACY] Main saved photo targets: %s applied, %s failed",
                main_privacy["applied"],
                main_privacy["failed"],
            )
        except Exception as e:
            logger.error("[PRIVACY] Default policy failed: %s", e, exc_info=True)

        
        
        
        mode = "normal" if self.normal_mode else "clone"
        logger.info(
            "[PROFILE-MODE] Startup mode restored as %s; profile apply skipped",
            mode,
        )

        return True

    async def _safe_disconnect_main(self):
        if self.main_client is not None:
            try:
                await self.main_client.disconnect()
            except Exception:
                pass
            self.main_client = None

    async def _resolve_admin_id(self):
        """Use the connected Main account as the sole administrator."""
        if self.main_client is None:
            self._admin_user_id = None
            return
        try:
            main_user = await self.main_client.get_me()
            self._admin_user_id = main_user.id
            logger.info(
                "[AUTH] Main account is the sole admin: ID %s",
                self._admin_user_id,
            )
        except Exception as exc:
            logger.error("[AUTH] Cannot identify Main as admin: %s", exc)
            self._admin_user_id = None

    async def _load_commander_identities(self):
        from commander_manager import (
            active_name,
            list_commanders,
            path_for,
            register_identity,
        )

        active = active_name()
        for name in list_commanders():
            if name == active:
                continue
            client = None
            try:
                session_path = path_for(name)
                client = TelegramClient(
                    str(session_path.with_suffix("")),
                    API_ID,
                    API_HASH,
                )
                await client.connect()
                if not await client.is_user_authorized():
                    await client.disconnect()
                    continue
                user = await client.get_me()
                self.commander_clients[name] = client
                register_identity(name, user.id)
                logger.info(
                    "[COMMANDER] %s identity loaded: ID %s",
                    name,
                    user.id,
                )
            except Exception as exc:
                logger.warning(
                    "[COMMANDER] Could not load %s: %s",
                    name,
                    exc,
                )
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

    
    
    

    async def _resume_after_startup(self):
        await asyncio.sleep(3)
        try:
            from handlers.go_handler import resume_join_job

            async def _resume_go_in_background():
                try:
                    await resume_join_job(self)
                except Exception as exc:
                    logger.error("[GO] Resume failed: %s", exc, exc_info=True)

            
            
            
            asyncio.create_task(
                _resume_go_in_background(), name="resume-go-join"
            )
        except Exception as exc:
            logger.error("[GO] Resume failed: %s", exc, exc_info=True)
        try:
            from handlers.send_handler import (
                resume_all_loops,
                resume_all_sequentials,
            )
            from handlers.click_handler import resume_all_hunt_clicks
            from handlers.dclick_handler import resume_persisted_dclicks
            from handlers.forward_handler import resume_persisted_forwards
            from handlers.rep_handler import resume_persisted_rep_jobs

            logger.info("=" * 60)
            logger.info("[RESUME] Starting resume of interrupted tasks…")
            logger.info("=" * 60)

            await resume_all_loops(self)
            await resume_all_sequentials(self)
            await resume_all_hunt_clicks(self)
            await resume_persisted_dclicks(self)
            await resume_persisted_forwards(self)
            await resume_persisted_rep_jobs(self)

            logger.info("[RESUME] ✓ All tasks resumed")
        except Exception as e:
            logger.error(f"[RESUME] ✗ Failed: {e}", exc_info=True)

    
    
    

    def _start_clone_manager(self):
        """Start Clone Manager inline bot in background."""
        try:
            from clone_settings import start_clone_manager
            start_clone_manager(self)
            logger.info("[CLONE-MGR] ✓ Clone Manager bot started")
        except Exception as e:
            logger.error(f"[CLONE-MGR] ✗ Failed to start: {e}")

    
    
    

    async def run(self):
        self._event_loop = asyncio.get_running_loop()
        
        setup_web_logging()
        set_web_automation(self)
        run_web_server(host="0.0.0.0", port=1500)
        logger.info("[WEB] ✓ Public dashboard on http://0.0.0.0:1500")

        
        set_api_automation(self)
        run_admin_api()

        
        asyncio.create_task(self._reload_watcher())

        
        try:
            success = await self.initialize()
        except Exception as e:
            success = False
            self.startup_error = f"Fatal init: {type(e).__name__}: {e}"
            logger.error(f"[INIT] ✗ {self.startup_error}", exc_info=True)

        print_banner()

        if success and self.main_client is not None:
            try:
                register_all_handlers(self)
                logger.info("[HANDLERS] ✓ All handlers registered")

                
                try:
                    from state_persistence import (
                        init_state, cleanup_stale_state,
                    )
                    init_state()
                    cleanup_stale_state()
                    logger.info("[STATE] ✓ State persistence initialized")
                except Exception as e:
                    logger.error(f"[STATE] Init failed: {e}")

                
                asyncio.create_task(self._resume_after_startup())

                
                self._start_clone_manager()

                self.bot_running = True

                print_status_box([
                    f"{C.BGREEN}Admin{C.RESET}       : "
                    f"{C.BWHITE}Main account{C.RESET} "
                    f"(ID={self._admin_user_id})",
                    f"{C.BCYAN}Clones{C.RESET}      : "
                    f"{C.BWHITE}{len(self.clone_clients)}{C.RESET}",
                    f"{C.BYELLOW}Bridge{C.RESET}      : "
                    f"{C.BWHITE}{BRIDGE_GROUP}{C.RESET}",
                    f"{C.BCYAN}WebPanel{C.RESET}    : "
                    f"{C.BWHITE}http://0.0.0.0:1500{C.RESET} "
                    f"{C.DIM}(limited view + music){C.RESET}",
                    f"{C.BMAGENTA}Admin CLI{C.RESET}   : "
                    f"{C.BWHITE}python cli_client.py{C.RESET}",
                    f"{C.BCYAN}Clone Mgr{C.RESET}   : "
                    f"{C.BWHITE}@clone_manager_bot{C.RESET} "
                    f"{C.DIM}(inline settings){C.RESET}",
                    "",
                    f"{C.DIM}Bot is running.{C.RESET}",
                ], title="SYSTEM READY")

                try:
                    await self.main_client.run_until_disconnected()
                    await self._handle_unexpected_main_disconnect(
                        "main run loop ended"
                    )
                except Exception as e:
                    logger.error(f"[MAIN] Disconnected: {e}")
                    await self._handle_unexpected_main_disconnect(
                        f"{type(e).__name__}: {e}"
                    )

                
                while True:
                    await asyncio.sleep(3600)

            except Exception as e:
                logger.error(
                    f"[INIT] Handler setup failed: {e}", exc_info=True,
                )
                self.startup_error = str(e)
                self.bot_running = False

        
        logger.warning("=" * 60)
        logger.warning("BOT IS NOT RUNNING — Web + Admin API only mode")
        logger.warning("=" * 60)
        if self.startup_error:
            logger.warning(f"Reason: {self.startup_error}")
        logger.warning("")
        logger.warning("Use admin CLI to fix:")
        logger.warning("  python cli_client.py")
        logger.warning("=" * 60)

        print_status_box([
            f"{C.BRED}Status{C.RESET}      : "
            f"{C.BWHITE}Bot NOT running{C.RESET}",
            f"{C.BYELLOW}Reason{C.RESET}      : {C.DIM}"
            f"{(self.startup_error or 'Unknown')[:50]}…{C.RESET}",
            "",
            f"{C.BCYAN}WebPanel{C.RESET}    : "
            f"{C.BWHITE}http://0.0.0.0:1500{C.RESET}",
            f"{C.BMAGENTA}Admin CLI{C.RESET}   : "
            f"{C.BWHITE}python cli_client.py{C.RESET}",
            "",
            f"{C.BGREEN}Fix via CLI:{C.RESET}",
            f"  1. python cli_client.py",
            f"  2. Choose: Create Session → Main",
            f"  3. Enter phone + OTP",
            f"  4. Reload bot",
        ], title="WEB-ONLY MODE")

        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass

    
    
    

    async def shutdown(self):
        self._shutdown_requested = True
        logger.info("[INIT] Shutting down…")

        if temp_main.active:
            temp_main.deactivate()
            self._temp_main_active = False
            self._temp_main_user_id = None

        
        try:
            from clone_settings.bot_core import stop_bot
            await stop_bot()
        except Exception:
            pass

        try:
            await self.loops.cancel_all()
        except Exception:
            pass

        for client in self.clone_clients:
            try:
                await client.disconnect()
            except Exception:
                pass

        if self.main_client:
            try:
                await self.main_client.disconnect()
            except Exception:
                pass

        logger.info("[INIT] ✓ Shutdown complete (state preserved for resume)")






async def main():
    try:
        validate_config()
    except Exception as e:
        logger.error(f"[CONFIG] ✗ Invalid config: {e}")
        sys.exit(1)

    bot = TelegramAutomation()
    try:
        await bot.run()
    except KeyboardInterrupt:
        logger.info(
            "[INIT] Keyboard interrupt — saving state and shutting down…"
        )
    except Exception as e:
        logger.error(f"[INIT] ✗ Fatal in main: {e}", exc_info=True)
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INIT] Forced exit")
        sys.exit(0)

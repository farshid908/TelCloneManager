
"""
Standalone bot runner.
Runs the Telegram bot as an independent process.
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)






def _auto_install():
    import subprocess
    required = {
        "telethon": "telethon==1.44.0",
        "flask": "flask==3.0.3",
        "aiogram": "aiogram==3.30.0",
    }
    missing = []
    for mod, pip_name in required.items():
        try:
            __import__(mod)
            if mod == "aiogram":
                from importlib.metadata import version
                if version("aiogram") != "3.30.0":
                    raise ImportError("aiogram version mismatch")
        except ImportError:
            missing.append(pip_name)

    if missing:
        for pkg in missing:
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--user", pkg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
            except Exception:
                pass


_auto_install()






import asyncio
import json
import logging
from typing import List, Optional

from telethon import TelegramClient

from config import (
    API_ID, API_HASH,
)
from logger_setup import setup_logging
from session_loader import discover_sessions
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
from state_persistence import (
    init_state,
    cleanup_stale_state,
)






setup_logging()
logger = logging.getLogger("TG-Auto")

LOG_FILE = os.path.join(BASE_DIR, "bot.log")

file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
)
file_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(file_handler)






STATUS_FILE = os.path.join(BASE_DIR, "bot_status.json")


def write_status(bot):
    """Write current bot status to shared JSON file for dashboard."""
    try:
        data = {
            "mirror_mode": bot.mirror_mode,
            "clones": [
                {
                    "index": i + 1,
                    "name": name,
                    "online": client.is_connected(),
                    "username": None,
                }
                for i, (client, name) in enumerate(
                    zip(bot.clone_clients, bot.clone_names)
                )
            ],
            "loops": [
                {"chat_id": cid, "text": txt}
                for (cid, txt) in bot.loops.active.keys()
            ],
            "admin_id": bot._admin_user_id,
        }
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"[STATUS] Write failed: {e}")






class TelegramAutomation:

    def __init__(self):
        self.main_client: Optional[TelegramClient] = None
        self.clone_clients: List[TelegramClient] = []
        self.clone_names: List[str] = []
        self._admin_user_id: Optional[int] = None
        self.mirror_mode: bool = False
        try:
            from clone_settings.normal_mode_store import (
                load_normal_mode_enabled,
            )
            self.normal_mode = load_normal_mode_enabled()
        except Exception:
            self.normal_mode = False
        self.loops: LoopRegistry = LoopRegistry()

    async def initialize(self):
        
        
        try:
            from session_manager import REPAIR_STATE_FILE, repair_clone_sessions
            if os.path.isfile(REPAIR_STATE_FILE):
                logger.warning("[REPAIR] Resuming interrupted Session Repair")
                await repair_clone_sessions()
        except Exception as exc:
            logger.error("[REPAIR] Resume failed: %s", exc, exc_info=True)
            return False

        main_path, clone_paths = discover_sessions()

        self.main_client = TelegramClient(main_path, API_ID, API_HASH)
        await self.main_client.start()
        main_me = await self.main_client.get_me()
        logger.info(
            f"[MAIN] ✓ Connected: {main_me.first_name} "
            f"(@{main_me.username}, ID={main_me.id})"
        )

        await self._resolve_admin_id()

        for cp in clone_paths:
            name = os.path.basename(cp)
            try:
                client = TelegramClient(cp, API_ID, API_HASH)
                await client.start()
                me = await client.get_me()
                self.clone_clients.append(client)
                self.clone_names.append(name)
                logger.info(
                    f"[CLONE] ✓ {name}: {me.first_name} (@{me.username})"
                )
            except Exception as e:
                logger.error(f"[CLONE] ✗ Failed '{name}': {e}")

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

        try:
            from clone_settings.profile_modes import apply_saved_mode
            mode = "normal" if self.normal_mode else "clone"
            results = await apply_saved_mode(self, mode)
            logger.info(
                "[PROFILE-MODE] Restored %s mode: %s applied, %s skipped, %s failed",
                mode,
                results["success"],
                results["skipped"],
                results["failed"],
            )
        except Exception as e:
            logger.error("[PROFILE-MODE] Restore failed: %s", e, exc_info=True)

    async def _resolve_admin_id(self):
        """Use the connected Main account as the sole administrator."""
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

    def is_admin(self, event) -> bool:
        return (
            self._admin_user_id is not None
            and event.sender_id == self._admin_user_id
        )

    async def status_writer_loop(self):
        """Periodically write status to shared file for the dashboard."""
        while True:
            try:
                write_status(self)
            except Exception as e:
                logger.error(f"[STATUS] Loop error: {e}")
            await asyncio.sleep(5)

    async def _resume_after_startup(self):
        """Resume interrupted loops, sequential sends, and hunt-clicks."""
        await asyncio.sleep(3)
        try:
            from handlers.go_handler import resume_join_job
            await resume_join_job(self)
        except Exception as exc:
            logger.error("[GO] Resume failed: %s", exc, exc_info=True)
        try:
            from handlers.send_handler import (
                resume_all_loops,
                resume_all_sequentials,
            )
            from handlers.click_handler import resume_all_hunt_clicks
            from handlers.rep_handler import resume_persisted_rep_jobs

            logger.info("[RESUME] Starting resume of interrupted tasks…")
            await resume_all_loops(self)
            await resume_all_sequentials(self)
            await resume_all_hunt_clicks(self)
            await resume_persisted_rep_jobs(self)
            logger.info("[RESUME] ✓ All tasks resumed")
        except Exception as e:
            logger.error(f"[RESUME] ✗ Failed: {e}", exc_info=True)

    async def run(self):
        await self.initialize()

        
        try:
            setup_web_logging()
            set_web_automation(self)
            run_web_server(host="0.0.0.0", port=1500)
            logger.info("[WEB] ✓ Public dashboard on http://0.0.0.0:1500")
        except Exception as e:
            logger.error(f"[WEB] Failed to start dashboard: {e}")

        try:
            set_api_automation(self)
            run_admin_api()
            logger.info("[ADMIN-API] ✓ Internal admin API started")
        except Exception as e:
            logger.error(f"[ADMIN-API] Failed to start: {e}")

        register_all_handlers(self)
        logger.info("[HANDLERS] ✓ All handlers registered")
        logger.info("[BOT] ✓ Telegram bot is running")

        try:
            init_state()
            cleanup_stale_state()
            logger.info("[STATE] ✓ State persistence initialized")
        except Exception as e:
            logger.error(f"[STATE] Init failed: {e}")

        asyncio.create_task(self.status_writer_loop())
        asyncio.create_task(self._resume_after_startup())

        await self.main_client.run_until_disconnected()

    async def shutdown(self):
        logger.info("[INIT] Shutting down…")
        await self.loops.cancel_all()

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

        logger.info("[INIT] ✓ Shutdown complete")






async def main():
    bot = TelegramAutomation()
    try:
        await bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"[FATAL] {e}", exc_info=True)
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Starting Telegram Automation Bot Runner")
    logger.info(f"PID: {os.getpid()}")
    logger.info("=" * 60)
    asyncio.run(main())

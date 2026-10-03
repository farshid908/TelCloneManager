"""
Clone Manager — Inline Bot for managing clone settings.

This package provides a Telegram Bot (via BotFather token) that allows
the admin to manage all clone accounts through inline queries and
inline keyboard buttons.

Features (by phase):
  Phase 1: Clone list, status, privacy, profile photo, name/bio
  Phase 2: Bulk actions, join/leave, loop management
  Phase 3: Analytics, safety monitoring, config

Architecture:
  - bot_core.py          → Bot client setup + polling
  - inline_handler.py    → Handles @bot inline queries
  - callback_handler.py  → Handles inline keyboard button presses
  - menus/               → Menu builders (keyboards + text)
  - actions/             → Actual operations on clones (via Telethon)
  - utils/               → Shared keyboards, formatters
"""

__TCM_FILE_HASH__ = "6170152393"


import logging

logger = logging.getLogger("CloneManager")


def start_clone_manager(automation):
    """
    Schedule the Clone Manager bot on the application's event loop.

    Args:
        automation: The TelegramAutomation instance from main.py
                    Provides access to main_client, clone_clients, etc.
    """
    from .bot_core import run_clone_manager_bot

    try:
        run_clone_manager_bot(automation)
        logger.info("[CLONE-MGR] ✓ Clone Manager bot started")
    except Exception as e:
        logger.error(f"[CLONE-MGR] ✗ Failed to start: {e}")

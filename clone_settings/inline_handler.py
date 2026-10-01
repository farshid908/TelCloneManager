"""Inline query handlers for the aiogram Clone Manager bot."""

import logging

from aiogram import Router
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

from .bot_core import can_access_normal_menu, get_automation, is_admin
from command_parser import extract_join_target
logger = logging.getLogger("CloneManager")


def register_inline_handlers(dispatcher):
    router = Router(name="clone_manager_inline")

    @router.inline_query()
    async def _on_inline(query: InlineQuery):
        user_id = query.from_user.id if query.from_user else None
        try:
            normal_access = can_access_normal_menu(user_id)
            if not is_admin(user_id) and not normal_access:
                # Inline queries must be answered by Telegram's deadline, but
                # an empty result gives non-admin users no visible response.
                await query.answer(
                    results=[],
                    cache_time=0,
                    is_personal=True,
                )
                return

            automation = get_automation()
            if automation is None:
                await query.answer(
                    results=[],
                    cache_time=5,
                    is_personal=True,
                    switch_pm_text="⚠️ Bot not ready",
                    switch_pm_parameter="not_ready",
                )
                return

            from .menus.main_menu import build_main_menu
            if normal_access and not is_admin(user_id):
                from .menus.normal_mode import build_normal_main_menu
                menu_text, menu_keyboard = build_normal_main_menu(
                    automation,
                    inline=True,
                    delegated=True,
                )
                results = [
                    InlineQueryResultArticle(
                        id="normal_mode",
                        title="Normal Mode",
                        description="Open Normal Mode",
                        input_message_content=InputTextMessageContent(
                            message_text=menu_text,
                        ),
                        reply_markup=menu_keyboard,
                    )
                ]
                await query.answer(
                    results=results,
                    cache_time=0,
                    is_personal=True,
                )
                return

            query_text = (query.query or "").strip()
            if query_text.lower().startswith("go "):
                target_text = query_text[3:].strip()
                target = extract_join_target(target_text)
                if target:
                    label = target.get("hash") or target.get("username", "?")
                    menu_text = (
                        f"🚀 Joining {label} with "
                        f"{len(automation.clone_clients)} clone(s)…\n"
                        "Preparing...\n"
                        "[░░░░░░░░░░░░░░░░░░░░] 0%"
                    )
                    menu_keyboard = None
                else:
                    menu_text = "Unable to parse the join target."
                    menu_keyboard = None
            else:
                menu_text, menu_keyboard = build_main_menu(
                    automation,
                    user_id=user_id,
                )
            results = [
                InlineQueryResultArticle(
                    id="clone_manager",
                    title="Clone Manager",
                    description="Open Clone Manager",
                    input_message_content=InputTextMessageContent(
                        message_text=menu_text,
                    ),
                    reply_markup=menu_keyboard,
                )
            ]
            await query.answer(results=results, cache_time=0, is_personal=True)
        except Exception as exc:
            logger.error(f"[INLINE] Error: {type(exc).__name__}: {exc}", exc_info=True)
            try:
                await query.answer(
                    results=[],
                    cache_time=1,
                    is_personal=True,
                    switch_pm_text="⚠️ Inline error",
                    switch_pm_parameter="inline_error",
                )
            except Exception:
                logger.debug("[INLINE] Failed to send error response", exc_info=True)

    dispatcher.include_router(router)

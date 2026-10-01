"""
Handler for hunt-click commands: mainclick, cloneNclick, mainDclick, stopclick.
"""

import asyncio
import logging
import time
from typing import Dict
from telethon import events

from command_parser import parse_hunt_click_command, parse_stop_click_command
from click_hunter import (
    hunt_and_click,
    hunt_and_click_on_edit,
    hunt_and_click_until_disappear,
)
from state_persistence import (
    register_hunt_click,
    update_hunt_click_progress,
    unregister_hunt_click,
    get_all_hunt_clicks,
)

logger = logging.getLogger("TG-Auto")

_active_hunts: Dict[str, Dict] = {}


async def cancel_all_hunt_clicks():
    """Cancel active hunt-click tasks and remove their saved state."""
    items = list(_active_hunts.items())
    _active_hunts.clear()
    for task_id, info in items:
        task = info.get("task")
        if task is not None and not task.done():
            task.cancel()
    tasks = [info.get("task") for _, info in items if info.get("task")]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for task_id, _ in items:
        try:
            unregister_hunt_click(task_id)
        except Exception:
            logger.debug("[CLICK-CMD] Could not remove %s", task_id, exc_info=True)
    logger.info("[CLICK-CMD] Cancelled all hunt-click tasks")


def _resolve_client(automation, clone_index: int):
    if clone_index == 0:
        if automation.main_client is None:
            return None, None
        return automation.main_client, "Main"

    zero_idx = clone_index - 1
    if 0 <= zero_idx < len(automation.clone_clients):
        return (
            automation.clone_clients[zero_idx],
            automation.clone_names[zero_idx],
        )
    return None, None


def _mode_description(parsed: Dict) -> str:
    if parsed.get("disappear_mode") and parsed.get("loop_mode"):
        return "🔁 disappear + loop"
    if parsed.get("disappear_mode"):
        return "🗑 until disappear"
    if parsed.get("edit_mode") and parsed.get("loop_mode"):
        return "🔄 edit + loop"
    if parsed.get("edit_mode"):
        return "🔄 on edit"
    if parsed.get("loop_mode"):
        return "🔁 loop forever"
    return "▶ normal"


def _times_description(parsed: Dict) -> str:
    times = parsed["times"]
    if times == 0:
        return "∞ unlimited"
    return str(times)


async def _run_hunt(
    client, button_text, times, delay, session_name,
    edit_mode, loop_mode, disappear_mode,
    progress_callback, stop_flag, start_from=0,
):
    """Dispatch to the correct hunt function. Never raises."""
    try:
        if disappear_mode:
            return await hunt_and_click_until_disappear(
                client=client,
                button_text=button_text,
                delay=delay,
                session_name=session_name,
                progress_callback=progress_callback,
                initial_done=start_from,
                stop_flag=stop_flag,
                loop_mode=loop_mode,
            )
        elif edit_mode:
            return await hunt_and_click_on_edit(
                client=client,
                button_text=button_text,
                max_times=times,
                session_name=session_name,
                progress_callback=progress_callback,
                stop_flag=stop_flag,
                initial_done=start_from,
                loop_mode=loop_mode,
            )
        else:
            return await hunt_and_click(
                client=client,
                button_text=button_text,
                times=times,
                delay=delay,
                session_name=session_name,
                progress_callback=progress_callback,
                start_from=start_from,
                stop_flag=stop_flag,
                loop_mode=loop_mode,
            )
    except asyncio.CancelledError:
        logger.info(f"[HUNT-DISPATCH] [{session_name}] Cancelled")
        raise
    except Exception as e:
        logger.error(
            f"[HUNT-DISPATCH] [{session_name}] Error: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return start_from


def register_click_handler(automation):
    """Register mainclick / cloneNclick / stopclick handlers."""
    aid = automation._admin_user_id

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^(main|clone\d+)D?click""", from_users=aid,
    ))
    async def _click(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_hunt_click_command(event.raw_text)
            if not parsed:
                await event.reply(
                    "⚠️ **Invalid syntax.** Examples:\n"
                    "`mainclick \"hello\"`\n"
                    "`mainclick3 \"hello\" /5`\n"
                    "`mainclick \"hello\" /edit`\n"
                    "`mainclick \"hello\" loop`\n"
                    "`mainclick \"hi\" /3 loop`\n"
                    "`mainDclick \"hi\" /3`\n"
                    "`mainDclick \"hi\" /3 loop`\n"
                    "`clone1click5 \"start\" /3`"
                )
                return

            session_ref = parsed["session_ref"]
            clone_index = parsed["clone_index"]
            times = parsed["times"]
            button_text = parsed["button_text"]
            delay = parsed["delay"]
            edit_mode = parsed["edit_mode"]
            loop_mode = parsed["loop_mode"]
            disappear_mode = parsed["disappear_mode"]

            client, session_name = _resolve_client(automation, clone_index)

            if client is None:
                await event.reply(
                    f"⚠️ Session **{session_ref}** not found or not connected."
                )
                return

            mode_desc = _mode_description(parsed)
            times_desc = _times_description(parsed)

            await event.reply(
                f"🎯 **Hunt-Click Started**\n"
                f"Session : `{session_name}`\n"
                f"Button  : `{button_text}`\n"
                f"Times   : {times_desc}\n"
                f"Delay   : {delay}s\n"
                f"Mode    : {mode_desc}\n\n"
                f"Stop with: `stopclick \"{button_text}\"` or `stopclick`"
            )

            logger.info(
                f"[CLICK-CMD] [{session_name}] Hunt \"{button_text}\" × {times_desc} "
                f"delay={delay}s edit={edit_mode} loop={loop_mode} disappear={disappear_mode}"
            )

            
            try:
                task_id = register_hunt_click(
                    session_ref=session_ref,
                    clone_index=clone_index,
                    times=times,
                    button_text=button_text,
                    delay=delay,
                    done=0,
                    edit_mode=edit_mode,
                    loop_mode=loop_mode,
                    disappear_mode=disappear_mode,
                )
            except Exception as e:
                logger.error(f"[CLICK-CMD] State register failed: {e}")
                task_id = f"local_{int(time.time() * 1000)}"

            async def _on_progress(done_count):
                try:
                    update_hunt_click_progress(task_id, done_count, delay)
                except Exception as e:
                    logger.error(f"[CLICK-CMD] Progress update failed: {e}")

            stop_flag = asyncio.Event()

            async def _runner():
                try:
                    await _run_hunt(
                        client=client,
                        button_text=button_text,
                        times=times,
                        delay=delay,
                        session_name=session_name,
                        edit_mode=edit_mode,
                        loop_mode=loop_mode,
                        disappear_mode=disappear_mode,
                        progress_callback=_on_progress,
                        stop_flag=stop_flag,
                        start_from=0,
                    )
                except asyncio.CancelledError:
                    logger.info(f"[CLICK-CMD] [{session_name}] Task cancelled")
                except Exception as e:
                    logger.error(
                        f"[CLICK-CMD] [{session_name}] Runner error: "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                finally:
                    
                    
                    
                    
                    if not (edit_mode or loop_mode or disappear_mode):
                        try:
                            unregister_hunt_click(task_id)
                        except Exception:
                            pass
                    _active_hunts.pop(task_id, None)
                    logger.info(f"[CLICK-CMD] [{session_name}] Task finished")

            task = asyncio.create_task(_runner())
            _active_hunts[task_id] = {
                "task": task,
                "stop_flag": stop_flag,
                "button": button_text,
                "session": session_name,
            }

        except Exception as e:
            logger.error(
                f"[CLICK-CMD] Handler error: {type(e).__name__}: {e}",
                exc_info=True,
            )
            try:
                await event.reply(f"⚠️ Error: {type(e).__name__}: {e}")
            except Exception:
                pass

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^stopclick""", from_users=aid,
    ))
    async def _stop_click(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_stop_click_command(event.raw_text)
            if not parsed:
                await event.reply(
                    "⚠️ Invalid. Use: `stopclick` or `stopclick \"button text\"`"
                )
                return

            target_button = parsed["button_text"]

            if not _active_hunts:
                await event.reply("ℹ️ No hunt-clicks running.")
                return

            stopped_count = 0
            stopped_details = []

            for task_id, info in list(_active_hunts.items()):
                if target_button is None or info["button"] == target_button:
                    try:
                        info["stop_flag"].set()
                        info["task"].cancel()
                        stopped_count += 1
                        stopped_details.append(
                            f"• `{info['session']}`: `{info['button']}`"
                        )
                    except Exception as e:
                        logger.error(f"[CLICK-CMD] Stop error: {e}")

            if stopped_count == 0:
                await event.reply(f"ℹ️ No hunt-click found for `{target_button}`")
            else:
                details = "\n".join(stopped_details)
                await event.reply(
                    f"🛑 **Stopped {stopped_count} hunt-click(s):**\n{details}"
                )
                logger.info(f"[CLICK-CMD] Stopped {stopped_count} hunt-click(s)")

        except Exception as e:
            logger.error(
                f"[CLICK-CMD] Stop handler error: {type(e).__name__}: {e}",
                exc_info=True,
            )






async def resume_all_hunt_clicks(automation):
    """Resume all interrupted hunt-click tasks."""
    try:
        tasks = get_all_hunt_clicks()
    except Exception as e:
        logger.error(f"[RESUME] Can't get hunt-clicks: {e}")
        return

    if not tasks:
        logger.info("[RESUME] No hunt-clicks to resume")
        return

    logger.info(f"[RESUME] Resuming {len(tasks)} hunt-click(s)…")
    now = time.time()

    for task_id, info in list(tasks.items()):
        try:
            session_ref = info["session_ref"]
            if info.get("kind") == "dclick":
                continue
            clone_index = info["clone_index"]
            times = info["times"]
            button_text = info["button_text"]
            delay = info["delay"]
            done = info.get("done", 0)
            next_at = info.get("next_click_at", now)
            edit_mode = info.get("edit_mode", False)
            loop_mode = info.get("loop_mode", False)
            disappear_mode = info.get("disappear_mode", False)

            client, session_name = _resolve_client(automation, clone_index)

            if client is None:
                logger.error(
                    f"[RESUME] Session {session_ref} not available for {task_id}"
                )
                try:
                    unregister_hunt_click(task_id)
                except Exception:
                    pass
                continue

            is_persistent = edit_mode or loop_mode or disappear_mode

            if not is_persistent and times > 0 and done >= times:
                logger.info(f"[RESUME] Hunt-click {task_id} already complete")
                try:
                    unregister_hunt_click(task_id)
                except Exception:
                    pass
                continue

            if not edit_mode:
                wait = next_at - now
                if wait > 0:
                    logger.info(
                        f"[RESUME] Hunt-click \"{button_text}\" — waiting "
                        f"{wait:.0f}s"
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.info(
                        f"[RESUME] Hunt-click \"{button_text}\" overdue "
                        f"({-wait:.0f}s) — running now"
                    )

            async def _on_progress(done_count, tid=task_id, d=delay):
                try:
                    update_hunt_click_progress(tid, done_count, d)
                except Exception as e:
                    logger.error(f"[RESUME] Progress error: {e}")

            logger.info(
                f"[RESUME] Resuming {task_id}: "
                f"mode=(edit={edit_mode}, loop={loop_mode}, disappear={disappear_mode}) "
                f"{done}/{times if times else '∞'} done"
            )

            stop_flag = asyncio.Event()

            async def _resume_runner(
                tid=task_id, c=client, sn=session_name,
                bt=button_text, tm=times, dl=delay, dn=done,
                em=edit_mode, lm=loop_mode, dm=disappear_mode,
                sf=stop_flag, cb=_on_progress,
            ):
                try:
                    await _run_hunt(
                        client=c,
                        button_text=bt,
                        times=tm,
                        delay=dl,
                        session_name=sn,
                        edit_mode=em,
                        loop_mode=lm,
                        disappear_mode=dm,
                        progress_callback=cb,
                        stop_flag=sf,
                        start_from=dn,
                    )
                except asyncio.CancelledError:
                    logger.info(f"[RESUME] [{sn}] Cancelled")
                except Exception as e:
                    logger.error(
                        f"[RESUME] [{sn}] Error: {type(e).__name__}: {e}",
                        exc_info=True,
                    )
                finally:
                    
                    
                    
                    if not (em or lm or dm):
                        try:
                            unregister_hunt_click(tid)
                        except Exception:
                            pass
                    _active_hunts.pop(tid, None)

            task = asyncio.create_task(_resume_runner())
            _active_hunts[task_id] = {
                "task": task,
                "stop_flag": stop_flag,
                "button": button_text,
                "session": session_name,
            }

        except Exception as e:
            logger.error(
                f"[RESUME] ✗ Failed to resume {task_id}: {e}",
                exc_info=True,
            )

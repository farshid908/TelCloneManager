import logging

from telethon import events

from config import BRIDGE_GROUP, BUTTON_CLICK_TIMEOUT
from state_persistence import get_all_loops, get_all_hunt_clicks
from handlers.forward_handler import _jobs as forward_jobs, _job_meta as forward_job_meta

logger = logging.getLogger("TG-Auto")


def register_status_handler(automation):
    """Register /status command handler."""
    aid = automation._admin_user_id

    @automation.main_client.on(events.NewMessage(
        pattern=r'^/status$', from_users=aid,
    ))
    async def _status(event):
        if not automation.is_admin(event):
            return

        bridge_display = (
            f"`{BRIDGE_GROUP}`" if BRIDGE_GROUP is not None
            else "disabled"
        )

        lines = [
            "📊 **System Status**",
            f"Admin   : Main account "
            f"(ID `{automation._admin_user_id}`)",
            f"Bridge  : {bridge_display}",
            f"BtnTime : {BUTTON_CLICK_TIMEOUT}s",
            f"ClickMode: new + edit + poll\n",
        ]

        try:
            m = await automation.main_client.get_me()
            lines.append(
                f"🟢 **Main** : {m.first_name} (@{m.username})"
            )
        except Exception as e:
            lines.append(f"🔴 **Main** : {e}")

        for client, name in zip(
            automation.clone_clients, automation.clone_names
        ):
            try:
                me = await client.get_me()
                lines.append(
                    f"🟢 **{name}** : {me.first_name} (@{me.username})"
                )
            except Exception as e:
                lines.append(f"🔴 **{name}** : {e}")

        lines.append(
            f"\n🪞 Mirror: "
            f"{'ON ✅' if automation.mirror_mode else 'OFF ❌'}"
        )

        if automation.loops.active:
            lines.append(
                f"\n🔄 **Active Loops** "
                f"({len(automation.loops.active)}):"
            )
            for (cid, txt) in automation.loops.active:
                lines.append(f'  • chat `{cid}` → `"{txt}"`')
        else:
            lines.append("\n🔄 **Loops**: None")

        saved_loops = get_all_loops()
        saved_dclicks = [
            info for info in get_all_hunt_clicks().values()
            if info.get("kind") == "dclick"
        ]
        lines.append(
            f"\n💾 Saved state: {len(saved_loops)} loop(s), "
            f"{len(saved_dclicks)} LoopDclick(s)"
        )
        for info in saved_dclicks:
            lines.append(
                f'  • chat `{info.get("chat_id")}` → '
                f'LoopDclick "{info.get("button_text", "")}" '
                f'(cursor {info.get("cursor", 0)})'
            )

        await event.reply("\n".join(lines))

    @automation.main_client.on(events.NewMessage(
        pattern=r"^loop\s+-list$", from_users=aid,
    ))
    async def _loop_list(event):
        if not automation.is_admin(event):
            return
        saved = get_all_loops()
        active = list(automation.loops.active)
        lines = ["📋 Loop list"]
        if not active and not saved:
            lines.append("\nNo active or saved loops.")
        if active:
            lines.append(f"\n🟢 Active ({len(active)}):")
            for chat_id, text in active:
                lines.append(f'• `{chat_id}` → "{text}"')
        if saved:
            lines.append(f"\n💾 Saved ({len(saved)}):")
            for item in saved.values():
                params = item.get("params", {})
                interval = params.get("loop_interval", "?")
                lines.append(
                    f'• `{item.get("chat_id")}` → "{item.get("text")}" '
                    f'(every {interval}s) | stop: `stop "{item.get("text")}"`'
                )
        try:
            await event.edit("\n".join(lines), parse_mode=None)
        except Exception:
            await event.reply("\n".join(lines), parse_mode=None)

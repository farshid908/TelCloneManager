"""
Handler for react commands:
  react "emoji"                              — reply to message, Main + all clones
  react "emoji" LINK                         — react on linked message
  react(1 3 5) "emoji"                       — specific clones
  react(all) "emoji" LINK                    — all sessions on linked message
  react "random" / "random+" / "random-"     — random emoji
  react "❤️🤣👍"                              — random from these

"""

import asyncio
import logging
import re

from telethon import events

from config import BRIDGE_GROUP
from command_parser import (
    parse_react_with_link_command,
)
from react_engine import (
    resolve_peer_and_react,
)

logger = logging.getLogger("TG-Auto")


TG_LINK_RE = re.compile(
    r'https?://t\.me/(?:c/(?P<chat_id>\d+)|(?P<username>\w+))/(?P<msg_id>\d+)'
)






def _select_reactors(automation, clone_indices):
    """Build session list based on clone selector."""
    sessions = []

    if clone_indices is None:
        if automation.main_client is not None:
            sessions.append((automation.main_client, "Main"))
        for c, n in zip(automation.clone_clients, automation.clone_names):
            sessions.append((c, n))

    elif clone_indices == "all":
        if automation.main_client is not None:
            sessions.append((automation.main_client, "Main"))
        for c, n in zip(automation.clone_clients, automation.clone_names):
            sessions.append((c, n))

    else:
        skipped = []
        for idx in clone_indices:
            zero_idx = idx - 1
            if 0 <= zero_idx < len(automation.clone_clients):
                sessions.append((
                    automation.clone_clients[zero_idx],
                    automation.clone_names[zero_idx],
                ))
            else:
                skipped.append(idx)
        if skipped:
            logger.warning(f"[REACT] Skipped invalid indices: {skipped}")

    return sessions


async def _resolve_link_ids(client, parsed):
    """Resolve link from parsed command to (chat_id, msg_id)."""
    if isinstance(parsed, str):
        match = TG_LINK_RE.search(parsed)
        if not match:
            return None, None
        parsed = {
            "chat_id": int(match.group("chat_id"))
            if match.group("chat_id") else None,
            "username": match.group("username"),
            "msg_id": int(match.group("msg_id")),
        }
    if parsed.get("chat_id"):
        raw_id = parsed["chat_id"]
        for cid in [raw_id, int(f"-100{raw_id}")]:
            try:
                await client.get_entity(cid)
                return cid, parsed["msg_id"]
            except Exception:
                continue
        return None, None

    elif parsed.get("username"):
        try:
            entity = await client.get_entity(parsed["username"])
            return entity.id, parsed["msg_id"]
        except Exception as e:
            logger.error(f"[REACT] Can't resolve @{parsed['username']}: {e}")
            return None, None

    return None, None






def register_react_handler(automation):
    """Register react handlers."""
    aid = automation._admin_user_id

    
    @automation.main_client.on(events.NewMessage(
        pattern=r"""^react[\s(]""", from_users=aid,
    ))
    async def _react(event):
        try:
            if not automation.is_admin(event):
                return

            parsed = parse_react_with_link_command(event.raw_text)
            if not parsed:
                await event.reply(
                    "⚠️ Usage:\n"
                    '`react "👍"` (reply to a message)\n'
                    '`react "❤️" https://t.me/c/123/456`\n'
                    '`react(1 3 5) "🔥"`\n'
                    '`react "random"` — completely random\n'
                    '`react "random+"` — random positive\n'
                    '`react "random-"` — random negative\n'
                    '`react "❤️🤣👍"` — random from these'
                )
                return

            emoji = parsed["emoji"]
            clone_indices = parsed["clone_indices"]
            links = parsed.get("links") or []
            targets = []

            if links:
                for link in links:
                    target = await _resolve_link_ids(
                        automation.main_client, link,
                    )
                    if target[0] is not None and target[1] is not None:
                        targets.append(target)
            else:
                if not event.is_reply:
                    warn = await event.reply(
                        "⚠️ Reply to a message, or provide a link:\n"
                        '`react "👍"` (reply)\n'
                        '`react "❤️" https://t.me/c/123/456`'
                    )
                    
                    await asyncio.sleep(2)
                    try:
                        await event.delete()
                        await warn.delete()
                    except Exception:
                        pass
                    return

                reply_msg = await event.get_reply_message()
                if reply_msg is None:
                    await event.reply("⚠️ Cannot find replied message")
                    return

                targets.append((event.chat_id, reply_msg.id))

            if not targets:
                await event.reply("âš ï¸ Cannot resolve any message link")
                return

            sessions = _select_reactors(automation, clone_indices)

            if not sessions:
                await event.reply("⚠️ No sessions selected")
                return

            
            try:
                await event.delete()
            except Exception:
                pass

            logger.info(
                f"[REACT-CMD] React \"{emoji}\" to {len(targets)} "
                f"message(s) using {len(sessions)} session(s)"
            )

            
            random_used = set()
            results = []
            for target_chat_id, target_msg_id in targets:
                tasks = []
                for c, n in sessions:
                    selected_emoji = emoji
                    random_mode = emoji.strip().lower()
                    if random_mode in ("random-+", "random+-"):
                        random_mode = "random"
                    if random_mode in ("random", "random+", "random-"):
                        from react_engine import (
                            ALL_REACTIONS,
                            POSITIVE_REACTIONS,
                            NEGATIVE_REACTIONS,
                        )
                        pools = {
                            "random": ALL_REACTIONS,
                            "random+": POSITIVE_REACTIONS,
                            "random-": NEGATIVE_REACTIONS,
                        }
                        pool = pools[random_mode]
                        available = [item for item in pool if item not in random_used]
                        if not available:
                            random_used.clear()
                            available = list(pool)
                        import random
                        selected_emoji = random.choice(available)
                        random_used.add(selected_emoji)
                    tasks.append(resolve_peer_and_react(
                        client=c,
                        chat_id=target_chat_id,
                        msg_id=target_msg_id,
                        emoji=selected_emoji,
                        session_name=n,
                        main_client=automation.main_client,
                        bridge_group_id=BRIDGE_GROUP,
                        all_clone_clients=automation.clone_clients,
                        all_clone_names=automation.clone_names,
                        skip_msg_id=None,
                    ))
                results.extend(await asyncio.gather(*tasks, return_exceptions=True))
            ok = sum(1 for r in results if r is True)
            fail = len(results) - ok

            logger.info(f"[REACT-CMD] Complete: ✓{ok} ✗{fail}")

        except Exception as e:
            logger.error(
                f"[REACT-CMD] Error: {type(e).__name__}: {e}",
                exc_info=True,
            )

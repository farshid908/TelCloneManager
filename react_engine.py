"""
React engine — sends emoji reactions to messages.

Supports:
  - Single emoji reactions
  - Multiple emoji (random pick per clone)
  - Random positive/negative/all reactions
  - Smart random: gets allowed reactions from Telegram API
  - Fallback: if emoji invalid, tries others
  - Per-session, per-chat reaction cache
"""

__TCM_FILE_HASH__ = "2024833917"


import asyncio
import random
import logging
from typing import List, Optional, Dict, Tuple

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji
from telethon.errors import (
    FloodWaitError,
    ReactionInvalidError,
    ReactionEmptyError,
    ChatWriteForbiddenError,
)

logger = logging.getLogger("TG-Auto")






POSITIVE_REACTIONS = [
    "👍", "❤️", "🔥", "🥰", "👏", "😁", "🤩", "🎉",
    "⚡", "💯", "🏆", "💪", "🙏", "😍", "💕", "🤗",
    "✨", "🥳", "💖", "👌",
]

NEGATIVE_REACTIONS = [
    "👎", "😢", "😡", "🤮", "💩", "🤡", "😱", "😤",
    "🙈", "😒", "🥴", "😑", "👀", "🤷",
]

ALL_REACTIONS = POSITIVE_REACTIONS + NEGATIVE_REACTIONS



_chat_reaction_cache: Dict[Tuple[str, int], List[str]] = {}






def _get_session_cache_key(client, session_name="Unknown") -> str:
    """Build a stable cache key for a client/session."""
    try:
        session = getattr(client, "session", None)
        filename = getattr(session, "filename", None)
        if filename:
            return f"session:{filename}"
    except Exception:
        pass
    return f"name:{session_name}"


def _get_cache_key(client, chat_id: int, session_name="Unknown") -> Tuple[str, int]:
    return (_get_session_cache_key(client, session_name), int(chat_id))


def _get_cached_reactions(client, chat_id: int, session_name="Unknown") -> List[str]:
    return list(_chat_reaction_cache.get(_get_cache_key(client, chat_id, session_name), []))


def _set_cached_reactions(
    client,
    chat_id: int,
    reactions: List[str],
    session_name="Unknown",
):
    unique = []
    seen = set()
    for r in reactions:
        if r and r not in seen:
            seen.add(r)
            unique.append(r)
    _chat_reaction_cache[_get_cache_key(client, chat_id, session_name)] = unique


def _append_cached_reaction(
    client,
    chat_id: int,
    emoji: str,
    session_name="Unknown",
):
    if not emoji:
        return
    key = _get_cache_key(client, chat_id, session_name)
    if key not in _chat_reaction_cache:
        _chat_reaction_cache[key] = []
    if emoji not in _chat_reaction_cache[key]:
        _chat_reaction_cache[key].append(emoji)






def extract_emojis(text):
    """
    Extract individual emojis from a string.
    Handles multi-byte emoji, variation selectors, ZWJ sequences.
    """
    result = []
    i = 0
    chars = list(text)

    while i < len(chars):
        char = chars[i]
        code = ord(char)

        if code > 127 and not char.isalnum() and not char.isspace():
            emoji = char

            while i + 1 < len(chars):
                next_char = chars[i + 1]
                next_code = ord(next_char)

                if next_code == 0xFE0F:
                    i += 1
                    emoji += next_char
                elif next_code == 0x200D:
                    i += 1
                    emoji += next_char
                    if i + 1 < len(chars):
                        i += 1
                        emoji += chars[i]
                elif 0x1F3FB <= next_code <= 0x1F3FF:
                    i += 1
                    emoji += next_char
                elif next_code == 0x20E3:
                    i += 1
                    emoji += next_char
                else:
                    break

            result.append(emoji)

        i += 1

    return result if result else [text]


def pick_emoji(emoji_input, allowed_reactions=None):
    """
    Pick an emoji based on input.

    If allowed_reactions is provided, random modes prefer that list.
    """
    emoji_input = emoji_input.strip()
    lower = emoji_input.lower()
    if lower in ("random-+", "random+-"):
        lower = "random"
    allowed_reactions = [e for e in (allowed_reactions or []) if e]

    if allowed_reactions:
        if lower == "random":
            return random.choice(allowed_reactions)

        if lower == "random+":
            positive = [e for e in allowed_reactions if e in POSITIVE_REACTIONS]
            return random.choice(positive) if positive else random.choice(allowed_reactions)

        if lower == "random-":
            negative = [e for e in allowed_reactions if e in NEGATIVE_REACTIONS]
            return random.choice(negative) if negative else random.choice(allowed_reactions)

    if lower == "random":
        return random.choice(ALL_REACTIONS)

    if lower == "random+":
        return random.choice(POSITIVE_REACTIONS)

    if lower == "random-":
        return random.choice(NEGATIVE_REACTIONS)

    emojis = extract_emojis(emoji_input)
    if len(emojis) > 1:
        if allowed_reactions:
            matching = [e for e in emojis if e in allowed_reactions]
            if matching:
                return random.choice(matching)
        return random.choice(emojis)

    return emoji_input






async def discover_chat_reactions(
    client,
    peer,
    chat_id,
    session_name="Unknown",
    skip_msg_id=None,
):
    """
    Get allowed reactions for a chat directly from Telegram API.
    Results are cached per session+chat.
    """
    cached = _get_cached_reactions(client, chat_id, session_name)
    if cached:
        return cached

    logger.info(
        f"[REACT] [{session_name}] Getting allowed reactions "
        f"for chat {chat_id}…"
    )

    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.types import (
            ChatReactionsAll,
            ChatReactionsSome,
            ChatReactionsNone,
            ReactionEmoji as ReactionEmojiType,
            Channel,
            Chat,
        )

        entity = await client.get_entity(chat_id)

        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
            full_chat = full.full_chat
        elif isinstance(entity, Chat):
            full = await client(GetFullChatRequest(entity.id))
            full_chat = full.full_chat
        else:
            logger.debug(
                f"[REACT] [{session_name}] Not a chat/channel, "
                f"allowing all reactions"
            )
            _set_cached_reactions(client, chat_id, ALL_REACTIONS, session_name)
            return list(ALL_REACTIONS)

        reactions = getattr(full_chat, "available_reactions", None)

        if reactions is None:
            logger.info(
                f"[REACT] [{session_name}] No reaction info → all allowed"
            )
            _set_cached_reactions(client, chat_id, ALL_REACTIONS, session_name)
            return list(ALL_REACTIONS)

        if isinstance(reactions, ChatReactionsNone):
            logger.info(
                f"[REACT] [{session_name}] Reactions disabled in this chat"
            )
            _set_cached_reactions(client, chat_id, [], session_name)
            return []

        if isinstance(reactions, ChatReactionsAll):
            logger.info(
                f"[REACT] [{session_name}] All reactions allowed"
            )
            _set_cached_reactions(client, chat_id, ALL_REACTIONS, session_name)
            return list(ALL_REACTIONS)

        if isinstance(reactions, ChatReactionsSome):
            allowed = []
            for r in reactions.reactions:
                if isinstance(r, ReactionEmojiType):
                    allowed.append(r.emoticon)

            if allowed:
                _set_cached_reactions(client, chat_id, allowed, session_name)
                logger.info(
                    f"[REACT] [{session_name}] Allowed reactions "
                    f"({len(allowed)}): {', '.join(allowed[:15])}"
                )
                return allowed
            else:
                logger.info(
                    f"[REACT] [{session_name}] Only custom emoji allowed"
                )
                _set_cached_reactions(client, chat_id, [], session_name)
                return []

        logger.warning(
            f"[REACT] [{session_name}] Unknown reaction type: "
            f"{type(reactions).__name__}"
        )
        _set_cached_reactions(client, chat_id, ALL_REACTIONS, session_name)
        return list(ALL_REACTIONS)

    except Exception as e:
        logger.warning(
            f"[REACT] [{session_name}] Can't get reaction info: "
            f"{type(e).__name__}: {e}"
        )
        return list(ALL_REACTIONS)






async def send_reaction(
    client,
    peer,
    msg_id,
    emoji,
    session_name="Unknown",
    chat_id=None,
    allowed_reactions=None,
):
    """
    Send an emoji reaction to a single message.
    If invalid, tries fallback emojis from allowed/cached list.
    """
    if not emoji or not emoji.strip():
        return False

    emoji = emoji.strip()

    try:
        await client(SendReactionRequest(
            peer=peer,
            msg_id=msg_id,
            reaction=[ReactionEmoji(emoticon=emoji)],
        ))
        logger.debug(
            f"[REACT] [{session_name}] ✓ \"{emoji}\" on msg {msg_id}"
        )

        if chat_id is not None:
            _append_cached_reaction(client, chat_id, emoji, session_name)

        return True

    except FloodWaitError as e:
        logger.warning(
            f"[REACT] [{session_name}] FloodWait {e.seconds}s"
        )
        await asyncio.sleep(e.seconds + 1)
        try:
            await client(SendReactionRequest(
                peer=peer,
                msg_id=msg_id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ))
            if chat_id is not None:
                _append_cached_reaction(client, chat_id, emoji, session_name)
            return True
        except Exception:
            return False

    except (ReactionInvalidError, ReactionEmptyError):
        fallback_pool = []

        if allowed_reactions:
            fallback_pool.extend(allowed_reactions)

        if chat_id is not None:
            fallback_pool.extend(
                _get_cached_reactions(client, chat_id, session_name)
            )

        fallback_pool.extend(["👍", "❤️", "🔥", "👏", "😁"])

        seen = set()
        unique_fallbacks = []
        for fallback in fallback_pool:
            if fallback and fallback != emoji and fallback not in seen:
                seen.add(fallback)
                unique_fallbacks.append(fallback)

        for fallback in unique_fallbacks:
            try:
                await client(SendReactionRequest(
                    peer=peer,
                    msg_id=msg_id,
                    reaction=[ReactionEmoji(emoticon=fallback)],
                ))
                logger.info(
                    f"[REACT] [{session_name}] \"{emoji}\" invalid, "
                    f"used \"{fallback}\" instead"
                )
                if chat_id is not None:
                    _append_cached_reaction(client, chat_id, fallback, session_name)
                return True
            except (ReactionInvalidError, ReactionEmptyError):
                continue
            except Exception:
                break

        logger.warning(
            f"[REACT] [{session_name}] No valid reaction found"
        )
        return False

    except ChatWriteForbiddenError:
        logger.error(
            f"[REACT] [{session_name}] Cannot react in this chat"
        )
        return False

    except Exception as e:
        logger.error(
            f"[REACT] [{session_name}] Failed: {type(e).__name__}: {e}"
        )
        return False






async def resolve_peer_and_react(
    client,
    chat_id,
    msg_id,
    emoji,
    session_name="Unknown",
    main_client=None,
    bridge_group_id=None,
    all_clone_clients=None,
    all_clone_names=None,
    skip_msg_id=None,
):
    """
    Resolve peer then send reaction with smart emoji picking.
    Uses Telegram API to discover allowed reactions.
    """
    from entity_resolver import resolve_entity, resolve_entity_with_bridge

    peer = await resolve_entity(client, chat_id, session_name, silent=True)

    if peer is None and main_client and bridge_group_id:
        peer = await resolve_entity_with_bridge(
            client=client,
            target=chat_id,
            session_name=session_name,
            main_client=main_client,
            bridge_group_id=bridge_group_id,
            other_clones=all_clone_clients,
            other_names=all_clone_names,
        )

    if peer is None:
        logger.error(
            f"[REACT] [{session_name}] Cannot resolve chat {chat_id}"
        )
        return False

    allowed_reactions = None
    emoji_lower = emoji.strip().lower()
    if emoji_lower in ("random", "random+", "random-"):
        discover_client = main_client if main_client else client
        discover_session_name = "Main" if main_client else session_name
        discover_peer = None
        try:
            from entity_resolver import resolve_entity as _re
            discover_peer = await _re(
                discover_client, chat_id, discover_session_name, silent=True,
            )
        except Exception:
            discover_peer = peer

        if discover_peer:
            allowed_reactions = await discover_chat_reactions(
                discover_client,
                discover_peer,
                chat_id,
                session_name=discover_session_name,
                skip_msg_id=skip_msg_id,
            )

    actual_emoji = pick_emoji(emoji, allowed_reactions=allowed_reactions)

    return await send_reaction(
        client=client,
        peer=peer,
        msg_id=msg_id,
        emoji=actual_emoji,
        session_name=session_name,
        chat_id=chat_id,
        allowed_reactions=allowed_reactions,
    )

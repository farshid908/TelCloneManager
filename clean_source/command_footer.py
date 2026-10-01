"""Keep the original command visible on edited Telethon status messages."""

import html
import logging

from telethon import events

logger = logging.getLogger("TG-Auto")

_PATCHED_ATTR = "_tg_command_footer_patched"
_COMMAND_ATTR = "_tg_original_command"
_MAX_MESSAGE_LENGTH = 4096


def _with_command_footer(text, command, parse_mode):
    text = "" if text is None else str(text)
    command = (command or "").strip()
    if not command:
        return text, parse_mode

    footer = f"\n\n<code>{html.escape(command, quote=False)}</code>"
    if parse_mode == "html":
        body = text
    else:
        body = html.escape(text, quote=False)
        parse_mode = "html"

    available = _MAX_MESSAGE_LENGTH - len(footer)
    if available < 0:
        footer = footer[:_MAX_MESSAGE_LENGTH]
        available = 0
    return body[:available] + footer, parse_mode


def patch_event_edit(event):
    """Patch one outgoing event so every later edit carries its command."""
    if getattr(event, _PATCHED_ATTR, False):
        return
    command = (getattr(event, "raw_text", None) or "").strip()
    if not command:
        return

    original_edit = event.edit

    async def edit_with_command_footer(message, *args, **kwargs):
        rendered, parse_mode = _with_command_footer(
            message,
            command,
            kwargs.get("parse_mode"),
        )
        kwargs["parse_mode"] = parse_mode
        return await original_edit(rendered, *args, **kwargs)

    try:
        setattr(event, _COMMAND_ATTR, command)
        setattr(event, _PATCHED_ATTR, True)
        setattr(event, "edit", edit_with_command_footer)
    except Exception:
        logger.debug("[COMMAND] Could not attach command footer", exc_info=True)


def register_command_footer(automation):
    """Install the edit wrapper before the individual command handlers."""

    @automation.main_client.on(events.NewMessage(outgoing=True))
    async def _command_footer(event):
        patch_event_edit(event)


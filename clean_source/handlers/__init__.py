"""Handlers package."""

from .go_handler import register_go_handler
from .back_handler import register_back_handler
from .send_handler import register_send_handler
from .stop_handler import register_stop_handler
from .mirror_handler import register_mirror_handler
from .status_handler import register_status_handler
from .click_handler import register_click_handler
from .direct_click_handler import register_direct_click_handler
from .react_handler import register_react_handler
from .privacy_handler import register_privacy_handler
from .setprof_handler import register_setprof_handler
from .media_handler import register_media_handler
from .temp_main_handler import register_temp_main_handler
from .chatid_handler import register_chatid_handler
from .all_handler import register_all_handler
from .pool_handler import register_pool_handler
from .mode_handler import register_mode_handler
from .access_handler import register_access_handler
from .reply_handler import send_reply
from .mute_handler import register_mute_handler
from .dclick_handler import register_dclick_handler
from .forward_handler import register_forward_handler
from .rep_handler import register_rep_handler
from command_footer import register_command_footer


def register_all_handlers(automation):
    """Register all command handlers with the main client."""
    
    
    register_command_footer(automation)
    register_go_handler(automation)
    register_back_handler(automation)
    register_send_handler(automation)
    register_stop_handler(automation)
    register_mirror_handler(automation)
    register_status_handler(automation)
    register_click_handler(automation)
    register_direct_click_handler(automation)
    register_react_handler(automation)
    register_privacy_handler(automation)
    register_setprof_handler(automation)
    register_media_handler(automation)
    register_temp_main_handler(automation)
    register_chatid_handler(automation)
    register_all_handler(automation)
    register_pool_handler(automation)
    register_mode_handler(automation)
    register_access_handler(automation)
    register_mute_handler(automation)
    register_dclick_handler(automation)
    register_forward_handler(automation)
    register_rep_handler(automation)

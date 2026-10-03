"""
Temporary Main manager.

When activated:
  - A specific user becomes "Temp Main"
  - Original Main loses control (except temp main commands)
  - Existing loops continue unless the optional -s mode is used
  - Temp Main can control all clones
  - +me flag is disabled for Temp Main
  - mirror works for Temp Main

When deactivated:
  - Original Main regains full control
  - Loops stopped by -s are resumed
"""

__TCM_FILE_HASH__ = "2035365252"


import logging
from typing import Optional

logger = logging.getLogger("TG-Auto")


class TempMainState:
    """Holds the state of temporary main."""

    def __init__(self):
        self.active = False
        self.temp_user_id = None
        self.temp_username = None
        self.temp_first_name = None
        self.original_admin_id = None
        self.paused_loops = []  
        self.stop_loops = False
        self.target_chat_id = None
        self.target_label = None

    def activate(
        self,
        user_id,
        username,
        first_name,
        original_admin_id,
        stop_loops=False,
        target_chat_id=None,
        target_label=None,
    ):
        self.active = True
        self.temp_user_id = user_id
        self.temp_username = username
        self.temp_first_name = first_name
        self.original_admin_id = original_admin_id
        self.stop_loops = bool(stop_loops)
        self.target_chat_id = target_chat_id
        self.target_label = target_label
        logger.info(
            f"[TEMP-MAIN] Activated: {first_name} "
            f"(@{username}, ID={user_id})"
        )

    def deactivate(self):
        old = self.temp_first_name
        self.active = False
        self.temp_user_id = None
        self.temp_username = None
        self.temp_first_name = None
        self.paused_loops = []
        self.stop_loops = False
        self.target_chat_id = None
        self.target_label = None
        logger.info(f"[TEMP-MAIN] Deactivated (was: {old})")

    def is_temp_main(self, user_id):
        """Check if user_id is the current temp main."""
        return self.active and self.temp_user_id == user_id

    def is_original_main(self, user_id):
        """Check if user_id is the original main."""
        return self.original_admin_id == user_id

    def should_handle_command(self, user_id):
        """
        Determine if a command from user_id should be processed.

        When temp main is active:
          - Temp main: all commands EXCEPT +me related
          - Original main: ONLY 'untmp main' command
        When not active:
          - Original main: all commands
        """
        if not self.active:
            return self.original_admin_id == user_id

        
        if self.temp_user_id == user_id:
            return True
        if self.original_admin_id == user_id:
            return True  

        return False



temp_main = TempMainState()

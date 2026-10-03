
__TCM_FILE_HASH__ = "7353966409"

import os
import re
import glob
import logging
from typing import List, Tuple

from config import SESSIONS_DIR, MAIN_SESSION_NAME
from commander_manager import active_name, is_commander_name

logger = logging.getLogger("TG-Auto")


def _natural_sort_key(path: str):
    """Sort file paths by natural number order."""
    basename = os.path.basename(path)
    parts = re.split(r'(\d+)', basename)
    result = []
    for part in parts:
        if part.isdigit():
            result.append(int(part))
        else:
            result.append(part.lower())
    return result


def discover_sessions(sessions_dir: str = SESSIONS_DIR) -> Tuple[str, List[str]]:
    """
    Scan sessions directory and separate Main from Clone sessions.
    Sessions in _pending/ or starting with _ are ignored.
    """
    if not os.path.isdir(sessions_dir):
        raise FileNotFoundError(f"Sessions directory not found: '{sessions_dir}'")

    files = glob.glob(os.path.join(sessions_dir, "*.session"))
    if not files:
        raise FileNotFoundError(f"No .session files in '{sessions_dir}'")

    main_path = None
    clone_paths = []
    active_commander = active_name()

    for sf in files:
        base = os.path.splitext(os.path.basename(sf))[0]

        
        if base.startswith("_"):
            continue

        path = os.path.join(sessions_dir, base)
        if is_commander_name(base):
            if base.lower() == (active_commander or "").lower():
                main_path = path
            continue
        if base.lower() == MAIN_SESSION_NAME.lower():
            main_path = path
        else:
            clone_paths.append(path)

    if main_path is None:
        raise FileNotFoundError(
            f"'{MAIN_SESSION_NAME}.session' not found in '{sessions_dir}'"
        )

    clone_paths.sort(key=_natural_sort_key)

    logger.info(f"[DISCOVERY] Main → {os.path.basename(main_path)}")
    for i, cp in enumerate(clone_paths):
        logger.info(f"[DISCOVERY] Clone #{i + 1} → {os.path.basename(cp)}")
    logger.info(f"[DISCOVERY] Found 1 Main + {len(clone_paths)} Clone(s)")

    return main_path, clone_paths

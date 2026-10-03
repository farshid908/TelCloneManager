
__TCM_FILE_HASH__ = "7936212544"

import re
import sys
import logging
from typing import List
from colors import C


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors based on log level and content tags."""

    LEVEL_COLORS = {
        logging.DEBUG:    C.DIM + C.WHITE,
        logging.INFO:     C.BWHITE,
        logging.WARNING:  C.BYELLOW,
        logging.ERROR:    C.BRED,
        logging.CRITICAL: C.BOLD + C.BG_RED + C.BWHITE,
    }

    TAG_COLORS = {
        "MAIN":      C.BOLD + C.BGREEN,
        "CLONE":     C.BOLD + C.BCYAN,
        "SEND":      C.BOLD + C.BBLUE,
        "LOOP":      C.BOLD + C.BMAGENTA,
        "MIRROR":    C.BOLD + C.BYELLOW,
        "CLICK":     C.BOLD + C.BMAGENTA,
        "BRIDGE":    C.BOLD + C.CYAN,
        "RESOLVE":   C.BOLD + C.BLUE,
        "AUTH":      C.BOLD + C.GREEN,
        "HANDLERS":  C.BOLD + C.GREEN,
        "DISCOVERY": C.BOLD + C.CYAN,
        "CONFIG":    C.BOLD + C.YELLOW,
        "PARSE":     C.BOLD + C.WHITE,
        "INIT":      C.BOLD + C.BGREEN,
        "DELETE":    C.BOLD + C.BRED,
    }

    SYMBOL_MAP = {
        "✓": C.BGREEN + "✓" + C.RESET,
        "✗": C.BRED + "✗" + C.RESET,
        "⏰": C.BYELLOW + "⏰" + C.RESET,
        "🔘": C.BMAGENTA + "🔘" + C.RESET,
        "🔍": C.BCYAN + "🔍" + C.RESET,
        "🚀": C.BGREEN + "🚀" + C.RESET,
        "🛑": C.BRED + "🛑" + C.RESET,
        "⚠️": C.BYELLOW + "⚠️" + C.RESET,
        "🗑️": C.BRED + "🗑️" + C.RESET,
    }

    def format(self, record):
        time_str = self.formatTime(record, "%H:%M:%S")
        time_colored = f"{C.DIM}{time_str}{C.RESET}"

        level_color = self.LEVEL_COLORS.get(record.levelno, C.WHITE)
        level_str = f"{level_color}{record.levelname:<7}{C.RESET}"

        msg = record.getMessage()

        for tag, color in self.TAG_COLORS.items():
            msg = msg.replace(f"[{tag}]", f"{color}[{tag}]{C.RESET}")

        for symbol, colored in self.SYMBOL_MAP.items():
            msg = msg.replace(symbol, colored)

        msg = re.sub(
            r'\[(Clone\d+)\]',
            lambda m: f"{C.BOLD}{C.BCYAN}[{m.group(1)}]{C.RESET}",
            msg,
        )
        msg = re.sub(
            r'\[(Main)\]',
            lambda m: f"{C.BOLD}{C.BGREEN}[{m.group(1)}]{C.RESET}",
            msg,
        )
        msg = re.sub(
            r'"([^"]*)"',
            lambda m: f'{C.BYELLOW}"{m.group(1)}"{C.RESET}',
            msg,
        )
        msg = msg.replace("→", f"{C.DIM}→{C.RESET}")
        msg = msg.replace("—", f"{C.DIM}—{C.RESET}")

        separator = f"{C.DIM}│{C.RESET}"
        return f" {time_colored} {separator} {level_str} {separator} {msg}"


def setup_logging():
    """Configure colored logging for terminal."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColoredFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    logging.getLogger("telethon").setLevel(logging.WARNING)


def print_banner():
    """Print startup ASCII art banner."""
    banner = f"""
{C.BCYAN}{C.BOLD}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   {C.BWHITE}████████╗ ██████╗       █████╗ ██╗   ██╗████████╗ ██████╗  {C.BCYAN}║
║   {C.BWHITE}╚══██╔══╝██╔════╝      ██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗{C.BCYAN}║
║   {C.BWHITE}   ██║   ██║  ███╗     ███████║██║   ██║   ██║   ██║   ██║{C.BCYAN}║
║   {C.BWHITE}   ██║   ██║   ██║     ██╔══██║██║   ██║   ██║   ██║   ██║{C.BCYAN}║
║   {C.BWHITE}   ██║   ╚██████╔╝     ██║  ██║╚██████╔╝   ██║   ╚██████╔╝{C.BCYAN}║
║   {C.BWHITE}   ╚═╝    ╚═════╝      ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ {C.BCYAN}║
║                                                              ║
║   {C.BYELLOW}Multi-Account Telegram Automation System{C.BCYAN}                    ║
║   {C.DIM}Click Engine • Mirror Mode • Loop Control • Auto-Delete{C.BCYAN}    ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{C.RESET}
"""
    print(banner)


def print_status_box(lines: List[str], title: str = "STATUS"):
    """Print a colored bordered status box."""
    width = 58
    print(f"\n{C.BCYAN}┌{'─' * width}┐{C.RESET}")
    print(f"{C.BCYAN}│{C.BOLD}{C.BWHITE}  {title:^{width - 2}}{C.RESET}{C.BCYAN}│{C.RESET}")
    print(f"{C.BCYAN}├{'─' * width}┤{C.RESET}")
    for line in lines:
        clean = re.sub(r'\033\[[0-9;]*m', '', line)
        padding = width - 2 - len(clean)
        if padding < 0:
            padding = 0
        print(f"{C.BCYAN}│{C.RESET} {line}{' ' * padding} {C.BCYAN}│{C.RESET}")
    print(f"{C.BCYAN}└{'─' * width}┘{C.RESET}\n")

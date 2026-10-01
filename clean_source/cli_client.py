
"""
CLI client for the internal admin API.

Usage:
    python cli_client.py               # interactive menu
    python cli_client.py dashboard     # live log dashboard; F8 opens menu
    python cli_client.py status        # quick status
    python cli_client.py reload        # quick reload
    python cli_client.py logs          # quick logs
"""

import sys
import json
import os
import getpass
import time
import argparse
from urllib import request as urlreq
from urllib import parse as urlparse
from urllib.error import HTTPError, URLError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _load_dotenv():
    import os
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)

_load_dotenv()

from config import ADMIN_API_HOST, ADMIN_API_PORT, ADMIN_API_TOKEN






class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


BASE_URL = f"http://{ADMIN_API_HOST}:{ADMIN_API_PORT}"






def api_call(path, method="GET", data=None, timeout=20):
    """Make an API call."""
    url = BASE_URL + path

    headers = {
        "Authorization": f"Bearer {ADMIN_API_TOKEN}",
        "Content-Type": "application/json",
    }

    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = urlreq.Request(url, data=body, headers=headers, method=method)

    try:
        with urlreq.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                return {
                    "error": "API returned invalid JSON",
                    "status_code": resp.status,
                }
            if isinstance(result, dict):
                result.setdefault("status_code", resp.status)
            return result
    except HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8", errors="replace"))
            return {
                "error": err_body.get("error") or err_body.get("message") or str(e),
                "status_code": e.code,
            }
        except Exception:
            return {"error": str(e), "status_code": e.code}
    except URLError as e:
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


def _api_failed(response):
    return not isinstance(response, dict) or bool(response.get("error"))


def _show_api_error(response):
    if not isinstance(response, dict):
        print(f"{C.RED}API returned an invalid response{C.RESET}")
        return
    status = response.get("status_code")
    suffix = f" (HTTP {status})" if status else ""
    print(f"{C.RED}{response.get('error', 'Unknown API error')}{suffix}{C.RESET}")


def _read_2fa_password(prompt=None):
    """Read a 2FA password without echoing it; Ctrl+P toggles visibility."""
    prompt = prompt or (
        f"{C.YELLOW}2FA password (Ctrl + P to show 2FA): {C.RESET}"
    )

    
    if not sys.stdin.isatty():
        return getpass.getpass(prompt)

    try:
        import termios
        import tty
    except ImportError:
        return getpass.getpass(prompt)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    value = []
    visible = False

    def redraw():
        shown = "".join(value) if visible else "*" * len(value)
        sys.stdout.write("\r" + prompt + shown + "\033[K")
        sys.stdout.flush()

    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        tty.setraw(fd)
        while True:
            char = sys.stdin.read(1)
            if char in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(value)
            if char == "\x03":
                raise KeyboardInterrupt
            if char == "\x10":  
                visible = not visible
                redraw()
                continue
            if char in ("\x7f", "\x08"):
                if value:
                    value.pop()
                    redraw()
                continue
            if char.isprintable():
                value.append(char)
                sys.stdout.write(char if visible else "*")
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def check_connection():
    """Test connection to admin API."""
    r = api_call("/ping")
    if isinstance(r, dict) and r.get("pong"):
        return True
    print(f"{C.RED}✗ Cannot connect to admin API at {BASE_URL}{C.RESET}")
    if isinstance(r, dict):
        print(f"  Error: {r.get('error', 'unknown')}")
    else:
        print("  Error: API returned an invalid response")
    print(f"  Make sure main.py is running.")
    return False






def cmd_status():
    print(f"\n{C.CYAN}{C.BOLD}📊 System Status{C.RESET}")
    print("─" * 60)

    r = api_call("/status")
    if _api_failed(r):
        _show_api_error(r)
        return

    
    main_status = f"{C.GREEN}● ONLINE{C.RESET}" if r.get("main_online") else f"{C.RED}● OFFLINE{C.RESET}"
    print(f"{C.BOLD}Main:{C.RESET} {main_status}")
    if r.get("main_name"):
        print(f"  Name: {r['main_name']}")
        if r.get("main_username"):
            print(f"  Username: @{r['main_username']}")
        if r.get("main_id"):
            print(f"  ID: {r['main_id']}")

    
    total = r.get("total_clones", 0)
    online = r.get("online_clones", 0)
    print(f"\n{C.BOLD}Clones:{C.RESET} {C.GREEN}{online}{C.RESET}/{total} online")

    for c in r.get("clones", []):
        icon = f"{C.GREEN}●{C.RESET}" if c["online"] else f"{C.RED}●{C.RESET}"
        uname = f"@{c['username']}" if c.get("username") else "N/A"
        print(f"  {icon} #{c['index']:2} {c['name']:15} {uname}")

    
    mirror = f"{C.GREEN}ON{C.RESET}" if r.get("mirror_mode") else f"{C.RED}OFF{C.RESET}"
    print(f"\n{C.BOLD}Mirror:{C.RESET} {mirror}")

    
    loops = r.get("loops", [])
    print(f"\n{C.BOLD}Active Loops:{C.RESET} {len(loops)}")
    for l in loops:
        print(f"  • Chat {l['chat_id']}: \"{l['text']}\"")


def cmd_reload():
    print(f"\n{C.YELLOW}🔄 Reloading bot…{C.RESET}")
    r = api_call("/bot/reload", method="POST")

    if isinstance(r, dict) and r.get("success"):
        print(f"{C.GREEN}✓ {r.get('message', 'Reloaded')}{C.RESET}")
    else:
        message = r.get("message", "Failed") if isinstance(r, dict) else "Invalid API response"
        print(f"{C.RED}✗ {message}{C.RESET}")


def cmd_sessions():
    print(f"\n{C.CYAN}{C.BOLD}📂 Sessions{C.RESET}")
    print("─" * 60)

    r = api_call("/sessions/list")
    if _api_failed(r):
        _show_api_error(r)
        return

    print(f"{C.BOLD}Active Main:{C.RESET} {r.get('active_main')}")
    print(f"{C.BOLD}Bridge Link:{C.RESET} {r.get('bridge_link') or '(none)'}")

    print(f"\n{C.BOLD}Main Sessions:{C.RESET}")
    for m in r.get("mains", []):
        marker = f"{C.GREEN}● ACTIVE{C.RESET}" if m["active"] else f"{C.DIM}○ standby{C.RESET}"
        print(f"  {marker}  {m['name']}")

    print(f"\n{C.BOLD}Clone Sessions ({len(r.get('clones', []))}):{C.RESET}")
    for i, c in enumerate(r.get("clones", []), 1):
        print(f"  #{i:2}  {c['name']}")


def cmd_activate_main():
    r = api_call("/sessions/list")
    if _api_failed(r):
        _show_api_error(r)
        return

    mains = r.get("mains", [])
    if not mains:
        print(f"{C.RED}No mains available{C.RESET}")
        return

    print(f"\n{C.CYAN}Available Mains:{C.RESET}")
    for i, m in enumerate(mains, 1):
        marker = " (active)" if m["active"] else ""
        print(f"  {i}. {m['name']}{marker}")

    try:
        choice = int(input(f"\n{C.YELLOW}Select #: {C.RESET}").strip())
        if not 1 <= choice <= len(mains):
            print(f"{C.RED}Invalid choice{C.RESET}")
            return
    except ValueError:
        print(f"{C.RED}Invalid input{C.RESET}")
        return

    name = mains[choice - 1]["name"]

    if input(f"Activate '{name}'? [y/N]: ").strip().lower() != "y":
        print("Cancelled")
        return

    r = api_call("/sessions/activate", method="POST", data={"name": name})
    if isinstance(r, dict) and r.get("success"):
        print(f"{C.GREEN}✓ {r['message']}{C.RESET}")
        if input(f"Reload bot now? [Y/n]: ").strip().lower() != "n":
            cmd_reload()
    else:
        message = r.get("message") if isinstance(r, dict) else "Invalid API response"
        print(f"{C.RED}✗ {message}{C.RESET}")


def cmd_delete_session():
    r = api_call("/sessions/list")
    if _api_failed(r):
        _show_api_error(r)
        return

    all_items = []
    print(f"\n{C.CYAN}Sessions:{C.RESET}")
    idx = 1

    for m in r.get("mains", []):
        if not m["active"]:
            print(f"  {idx}. [MAIN]  {m['name']}")
            all_items.append(("main", m["name"]))
            idx += 1

    for c in r.get("clones", []):
        print(f"  {idx}. [CLONE] {c['name']}")
        all_items.append(("clone", c["name"]))
        idx += 1

    if not all_items:
        print("  (nothing deletable)")
        return

    try:
        choice = int(input(f"\n{C.YELLOW}Select # to delete: {C.RESET}").strip())
        if not 1 <= choice <= len(all_items):
            print(f"{C.RED}Invalid{C.RESET}")
            return
    except ValueError:
        print(f"{C.RED}Invalid{C.RESET}")
        return

    stype, name = all_items[choice - 1]

    if input(f"{C.RED}DELETE '{name}' ({stype})? [y/N]: {C.RESET}").strip().lower() != "y":
        print("Cancelled")
        return

    r = api_call("/sessions/delete", method="POST", data={"name": name, "type": stype})
    if r.get("success"):
        print(f"{C.GREEN}✓ {r['message']}{C.RESET}")
    else:
        print(f"{C.RED}✗ {r.get('message')}{C.RESET}")


def cmd_convert_session():
    r = api_call("/sessions/list")
    if _api_failed(r):
        _show_api_error(r)
        return

    convertible = []
    print(f"\n{C.CYAN}Convertible Sessions:{C.RESET}")
    idx = 1

    for m in r.get("mains", []):
        if not m["active"]:
            print(f"  {idx}. MAIN → CLONE:  {m['name']}")
            convertible.append(("main", "clone", m["name"]))
            idx += 1

    for c in r.get("clones", []):
        print(f"  {idx}. CLONE → MAIN: {c['name']}")
        convertible.append(("clone", "main", c["name"]))
        idx += 1

    if not convertible:
        print("  (nothing)")
        return

    try:
        choice = int(input(f"\n{C.YELLOW}Select #: {C.RESET}").strip())
        if not 1 <= choice <= len(convertible):
            print(f"{C.RED}Invalid{C.RESET}")
            return
    except ValueError:
        print(f"{C.RED}Invalid{C.RESET}")
        return

    src_type, tgt_type, name = convertible[choice - 1]

    r = api_call("/sessions/convert", method="POST", data={
        "source_name": name,
        "source_type": src_type,
        "target_type": tgt_type,
    })
    if isinstance(r, dict) and r.get("success"):
        print(f"{C.GREEN}✓ {r['message']}{C.RESET}")
    else:
        message = r.get("message") if isinstance(r, dict) else "Invalid API response"
        print(f"{C.RED}✗ {message}{C.RESET}")


def cmd_make_session():
    print(f"\n{C.CYAN}Create new session:{C.RESET}")
    print("  1. Main")
    print("  2. Clone")

    try:
        choice = int(input(f"{C.YELLOW}Type #: {C.RESET}").strip())
    except ValueError:
        return

    stype = "main" if choice == 1 else "clone" if choice == 2 else None
    if not stype:
        print(f"{C.RED}Invalid{C.RESET}")
        return

    phone = input(f"{C.YELLOW}Phone (+countrycode): {C.RESET}").strip()
    if not phone:
        print(f"{C.RED}Phone required{C.RESET}")
        return

    print(f"{C.DIM}Sending code…{C.RESET}")
    r = api_call(
        "/sessions/make/start",
        method="POST",
        data={"type": stype, "phone": phone},
        timeout=240,
    )

    if not isinstance(r, dict) or not r.get("success"):
        message = (
            r.get("message") or r.get("error") or "Unknown API error"
            if isinstance(r, dict)
            else "Invalid API response"
        )
        print(f"{C.RED}✗ {message}{C.RESET}")
        return

    session_id = r.get("session_id")
    if not session_id:
        print(f"{C.RED}✗ API did not return a session ID{C.RESET}")
        return

    print(f"{C.GREEN}✓ {r['message']}{C.RESET}")
    print(f"  Device: {r.get('device')}")
    print(f"  Name: {r.get('name')}")

    print(
        f"\n{C.YELLOW}A Telegram login code was sent to {phone}."
        f"{C.RESET}"
    )
    
    
    password = None

    for attempt in range(1, 4):
        code = input(
            f"{C.YELLOW}Enter OTP code"
            f" ({attempt}/3): {C.RESET}"
        ).strip()
        if not code:
            print(f"{C.RED}✗ OTP code is required{C.RESET}")
            continue

        print(f"{C.DIM}Verifying…{C.RESET}")
        r = api_call(
            "/sessions/make/complete",
            method="POST",
            data={
                "session_id": session_id,
                "code": code,
                "password": password,
            },
            timeout=240,
        )

        if isinstance(r, dict) and r.get("success"):
            break

        if isinstance(r, dict) and r.get("needs_password"):
            password = _read_2fa_password() or None
            if not password:
                print(f"{C.RED}✗ 2FA password is required{C.RESET}")
                continue
            r = api_call(
                "/sessions/make/complete",
                method="POST",
                data={
                    "session_id": session_id,
                    "code": code,
                    "password": password,
                },
                timeout=240,
            )
            if isinstance(r, dict) and r.get("success"):
                break

        message = (
            r.get("message") or r.get("error") or "Verification failed"
            if isinstance(r, dict)
            else "Invalid API response"
        )
        print(f"{C.RED}✗ {message}{C.RESET}")
        if attempt < 3:
            print(f"{C.YELLOW}Please enter the latest Telegram code again.{C.RESET}")
            continue
        break

    if isinstance(r, dict) and r.get("success"):
        info = r.get("info", {})
        print(f"{C.GREEN}✓ Session '{r['name']}' created!{C.RESET}")
        print(f"  Name: {info.get('first_name', '?')}")
        print(f"  Username: @{info.get('username', 'none')}")
        print(f"  ID: {info.get('id', '?')}")

        if input(f"\n{C.YELLOW}Reload bot? [Y/n]: {C.RESET}").strip().lower() != "n":
            cmd_reload()

    elif isinstance(r, dict) and r.get("needs_password"):
        print(f"{C.YELLOW}⚠ This account has 2FA. Try again with password.{C.RESET}")
    else:
        message = r.get("message") if isinstance(r, dict) else "Invalid API response"
        print(f"{C.RED}✗ {message}{C.RESET}")


def cmd_bridge_link():
    r = api_call("/sessions/list")
    current = r.get("bridge_link", "") if isinstance(r, dict) else ""
    print(f"\n{C.CYAN}Current bridge link:{C.RESET} {current or '(none)'}")

    new = input(f"{C.YELLOW}New link (empty to keep): {C.RESET}").strip()
    if not new:
        print("Kept current")
        return

    r = api_call("/sessions/bridge", method="POST", data={"link": new})
    if r.get("success"):
        print(f"{C.GREEN}✓ {r['message']}{C.RESET}")
    else:
        print(f"{C.RED}✗ {r.get('message')}{C.RESET}")


def cmd_repair_sessions():
    print(f"\n{C.CYAN}{C.BOLD}REPAIR SESSIONS{C.RESET}")
    print("-" * 72)
    print(
        "The bot will pause briefly while invalid clone sessions are "
        "removed and Clone numbers are compacted."
    )
    if input(f"{C.YELLOW}Continue? [y/N]: {C.RESET}").strip().lower() != "y":
        print("Cancelled")
        return

    print(f"{C.DIM}Checking sessions and restarting the bot...{C.RESET}")
    response = api_call("/sessions/repair", method="POST", timeout=360)
    if _api_failed(response) or not response.get("success"):
        _show_api_error(response)
        if isinstance(response, dict) and response.get("message"):
            print(f"{C.RED}{response['message']}{C.RESET}")
        return

    removed = response.get("removed", [])
    renamed = response.get("renamed", {})
    print(f"{C.GREEN}Repair completed successfully.{C.RESET}")
    print(f"  Active clones: {response.get('active_clones', 0)}")
    print(f"  Online clones: {response.get('online_clones', 0)}")
    print(f"  Registered phones: {response.get('registered_phones', 0)}")
    print(f"  Removed: {', '.join(removed) if removed else 'none'}")
    if renamed:
        print("  Renumbered:")
        for old, new in renamed.items():
            print(f"    {old} -> {new}")
    print(f"  {response.get('message', '')}")



def cmd_logs(limit=50):
    print(f"\n{C.CYAN}{C.BOLD}SYSTEM LOGS - LAST {limit}{C.RESET}")
    print("-" * 72)
    response = api_call(f"/logs?limit={max(1, min(int(limit), 1000))}")
    if _api_failed(response):
        _show_api_error(response)
        return
    for log in response.get("logs", []):
        level = str(log.get("level", "INFO"))
        color = {
            "ERROR": C.RED,
            "WARNING": C.YELLOW,
            "DEBUG": C.DIM,
        }.get(level, C.WHITE)
        stamp = log.get("time", "--:--:--")
        message = log.get("message", "")
        print(f"{C.DIM}{stamp}{C.RESET} {color}{level:<8}{C.RESET} {message}")


def cmd_live_logs():
    print(f"{C.CYAN}{C.BOLD}LIVE LOGS{C.RESET}  (Ctrl+C to stop)")
    print("-" * 72)
    seen = set()
    try:
        while True:
            response = api_call("/logs?limit=100", timeout=10)
            if not _api_failed(response):
                for log in response.get("logs", []):
                    key = (
                        log.get("time"),
                        log.get("level"),
                        log.get("message"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    level = str(log.get("level", "INFO"))
                    color = {
                        "ERROR": C.RED,
                        "WARNING": C.YELLOW,
                        "DEBUG": C.DIM,
                    }.get(level, C.WHITE)
                    print(
                        f"{C.DIM}{log.get('time', '--:--:--')}{C.RESET} "
                        f"{color}{level:<8}{C.RESET} {log.get('message', '')}"
                    )
                if len(seen) > 2000:
                    seen = set(list(seen)[-1000:])
            time.sleep(2)
    except KeyboardInterrupt:
        print(f"\n{C.DIM}Live logs stopped.{C.RESET}")


def cmd_packages():
    print(f"\n{C.CYAN}{C.BOLD}PACKAGE STATUS{C.RESET}")
    print("-" * 72)
    response = api_call("/packages")
    if _api_failed(response):
        _show_api_error(response)
        return
    for name, info in response.get("packages", {}).items():
        info = info if isinstance(info, dict) else {}
        installed = bool(info.get("installed"))
        marker = f"{C.GREEN}OK{C.RESET}" if installed else f"{C.RED}MISSING{C.RESET}"
        version = info.get("version") or "-"
        print(f"  {marker:<18} {name:<28} {version}")


def interactive_menu():
    if not check_connection():
        return
    actions = {
        "1": ("Status", cmd_status),
        "2": ("Logs", lambda: cmd_logs(50)),
        "3": ("Live logs", cmd_live_logs),
        "4": ("Reload bot", cmd_reload),
        "5": ("Packages", cmd_packages),
        "6": ("List sessions", cmd_sessions),
        "7": ("Create session", cmd_make_session),
        "8": ("Convert session", cmd_convert_session),
        "9": ("Activate main", cmd_activate_main),
        "10": ("Delete session", cmd_delete_session),
        "11": ("Repair sessions", cmd_repair_sessions),
        "12": ("Set bridge link", cmd_bridge_link),
    }
    while True:
        print(f"\n{C.CYAN}{C.BOLD}TG BOT ADMIN CLI{C.RESET}")
        print(f"{C.DIM}{BASE_URL}{C.RESET}")
        print("-" * 40)
        for key, (label, _) in actions.items():
            print(f"  {key:>2}. {label}")
        print("   0. Exit")
        try:
            choice = input(f"\n{C.YELLOW}Choose: {C.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye")
            return
        if choice == "0":
            print("Bye")
            return
        action = actions.get(choice)
        if action is None:
            print(f"{C.RED}Invalid choice{C.RESET}")
            continue
        try:
            action[1]()
        except KeyboardInterrupt:
            print(f"\n{C.DIM}Cancelled.{C.RESET}")
        except Exception as exc:
            print(f"{C.RED}Command failed: {exc}{C.RESET}")


def dashboard_mode():
    """Show live logs with an F8 shortcut for the existing CLI menu.

    This is deliberately a client-only view: it reads the admin API and never
    starts or restarts the Telegram bot.  ``curses`` is imported lazily so the
    normal CLI remains usable on systems without curses support.
    """
    try:
        import curses
    except ImportError as exc:
        print(
            f"{C.RED}Dashboard requires curses support: {exc}{C.RESET}\n"
            f"Run the normal menu with: python cli_client.py"
        )
        return

    def _render(stdscr):
        try:
            curses.curs_set(0)
        except curses.error:
            
            pass
        stdscr.nodelay(True)
        stdscr.keypad(True)
        last_refresh = 0.0
        logs = []
        error = None

        while True:
            now = time.monotonic()
            if now - last_refresh >= 1.0:
                response = api_call("/logs?limit=200", timeout=5)
                if _api_failed(response):
                    error = (
                        str(response.get("error", "API unavailable"))
                        if isinstance(response, dict)
                        else "Invalid API response"
                    )
                else:
                    error = None
                    logs = response.get("logs", [])[-200:]
                last_refresh = now

            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 3):
                return
            if key == curses.KEY_F8:
                
                curses.def_prog_mode()
                curses.endwin()
                try:
                    interactive_menu()
                finally:
                    curses.reset_prog_mode()
                    stdscr.clear()
                    stdscr.refresh()

            height, width = stdscr.getmaxyx()
            stdscr.erase()
            if height < 5 or width < 2:
                if height > 0 and width > 1:
                    stdscr.addnstr(0, 0, "Terminal too small", width - 1)
                stdscr.refresh()
                time.sleep(0.1)
                continue

            title = "TG BOT LOG DASHBOARD"
            stdscr.addnstr(0, 0, title, max(0, width - 1), curses.A_BOLD)
            stdscr.addnstr(
                1, 0,
                "Live logs  |  q: quit dashboard",
                max(0, width - 1),
                curses.A_DIM,
            )

            body_bottom = height - 3
            visible = logs[-max(1, body_bottom - 2):]
            start_row = max(2, body_bottom - len(visible))
            for row, log in enumerate(visible, start=start_row):
                level = str(log.get("level", "INFO"))
                stamp = str(log.get("time", "--:--:--"))
                message = str(log.get("message", ""))
                line = f"{stamp} {level:<8} {message}"
                attrs = curses.A_NORMAL
                if level == "ERROR":
                    attrs = curses.A_BOLD
                elif level in {"WARNING", "DEBUG"}:
                    attrs = curses.A_DIM
                if row < body_bottom:
                    stdscr.addnstr(row, 0, line, max(0, width - 1), attrs)

            separator = "─" * max(0, width - 1)
            stdscr.addnstr(body_bottom, 0, separator, max(0, width - 1), curses.A_DIM)
            if error:
                stdscr.addnstr(
                    body_bottom + 1, 0,
                    f"API: {error}", max(0, width - 1), curses.A_DIM,
                )
            stdscr.addnstr(
                height - 1, 0,
                "F8  open menu",
                max(0, width - 1),
                curses.A_DIM,
            )
            stdscr.refresh()
            time.sleep(0.05)

    try:
        curses.wrapper(_render)
    except KeyboardInterrupt:
        pass





def main():
    parser = argparse.ArgumentParser(
        description="Local Telegram bot administration CLI",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "dashboard",
            "status",
            "logs",
            "live",
            "reload",
            "sessions",
            "repair",
            "packages",
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="number of logs to display (default: 50)",
    )
    args = parser.parse_args()

    if not args.command:
        interactive_menu()
        return

    if not check_connection():
        raise SystemExit(2)

    commands = {
        "dashboard": dashboard_mode,
        "status": cmd_status,
        "logs": lambda: cmd_logs(args.limit),
        "live": cmd_live_logs,
        "reload": cmd_reload,
        "sessions": cmd_sessions,
        "repair": cmd_repair_sessions,
        "packages": cmd_packages,
    }
    commands[args.command]()


if __name__ == "__main__":
    main()

"""
Internal Admin API — accessible ONLY from localhost.

This handles all admin operations that used to be in the web dashboard:
  - Make/delete/convert sessions
  - Reload bot
  - Get logs
  - Check packages
  - Activate main
  - Bridge link

Access is protected by:
  1. Bind to 127.0.0.1 only (not reachable from internet)
  2. Bearer token in Authorization header
"""

import time
import logging
import threading
import asyncio
import subprocess
import sys
import collections
import re
from functools import wraps

from flask import Flask, jsonify, request

from config import ADMIN_API_HOST, ADMIN_API_PORT, ADMIN_API_TOKEN

logger = logging.getLogger("TG-Auto")

api_app = Flask(__name__)

_automation = None


admin_log_buffer = collections.deque(maxlen=1000)
ANSI_STRIP = re.compile(r'\033\[[0-9;]*m')

_bg_loop = None
_bg_thread = None
_bg_loop_lock = threading.Lock()
_logging_ready = False
_logging_lock = threading.Lock()


class AdminLogHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            clean_msg = ANSI_STRIP.sub('', msg)
            admin_log_buffer.append({
                "time": time.strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": clean_msg,
            })
        except Exception:
            pass


def setup_admin_logging():
    global _logging_ready
    with _logging_lock:
        if _logging_ready:
            return

        handler = AdminLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(handler)
        _logging_ready = True


def set_automation(automation):
    global _automation
    _automation = automation






def require_token(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        remote = request.remote_addr or ""

        
        if remote not in ("127.0.0.1", "::1"):
            logger.warning(f"[ADMIN-API] Blocked non-local access from {remote}")
            return jsonify({"error": "Forbidden"}), 403

        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {ADMIN_API_TOKEN}"

        if auth != expected:
            logger.warning(f"[ADMIN-API] Unauthorized access from {remote}")
            return jsonify({"error": "Unauthorized"}), 401

        return f(*args, **kwargs)
    return wrapper






def _start_bg_loop():
    global _bg_loop, _bg_thread

    if _bg_loop is not None:
        return

    with _bg_loop_lock:
        if _bg_loop is not None:
            return

        def _run():
            global _bg_loop
            loop = asyncio.new_event_loop()
            _bg_loop = loop
            asyncio.set_event_loop(loop)
            loop.run_forever()

        _bg_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="AdminBgLoop",
        )
        _bg_thread.start()

        started_at = time.time()
        while _bg_loop is None:
            if time.time() - started_at > 5:
                raise RuntimeError("Background admin asyncio loop failed to start")
            time.sleep(0.05)


def _run_async(coro, timeout=180):
    _start_bg_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    try:
        return fut.result(timeout=timeout)
    except Exception:
        try:
            fut.cancel()
        except Exception:
            pass
        raise






REQUIRED_PACKAGES = {
    "telethon": "telethon==1.44.0",
    "flask": "flask==3.0.3",
    "aiogram": "aiogram==3.30.0",
    "PIL": "Pillow>=10.0.0",
}


def check_packages():
    results = {}
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            mod = __import__(module_name)
            version = getattr(mod, "__version__", "unknown")
            results[pip_name] = {"installed": True, "version": version}
        except ImportError:
            results[pip_name] = {"installed": False, "version": None}

    try:
        r = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            version_line = r.stdout.split("\n")[0]
            parts = version_line.split()
            version = parts[2] if len(parts) > 2 else "?"
            results["ffmpeg"] = {"installed": True, "version": version}
        else:
            results["ffmpeg"] = {"installed": False, "version": None}
    except Exception:
        results["ffmpeg"] = {"installed": False, "version": None}

    return results


def install_package(pip_name):
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", pip_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "success": r.returncode == 0,
            "output": (r.stdout or "") + (r.stderr or ""),
        }
    except Exception as e:
        return {"success": False, "output": str(e)}






@api_app.route("/ping", methods=["GET"])
@require_token
def api_ping():
    return jsonify({"pong": True, "time": time.time()})






@api_app.route("/status", methods=["GET"])
@require_token
def api_status():
    if _automation is None:
        return jsonify({"error": "Automation not initialized"}), 503

    main_online = False
    main_username = None
    main_id = None
    main_name = "Main"

    if _automation.main_client is not None:
        try:
            main_online = _automation.main_client.is_connected()
        except Exception:
            pass

        try:
            if hasattr(_automation.main_client, "_self_user"):
                me = _automation.main_client._self_user
                if me:
                    main_username = me.username
                    main_id = me.id
                    main_name = me.first_name or "Main"
        except Exception:
            pass

    clones = []
    for i, (client, name) in enumerate(
        zip(_automation.clone_clients, _automation.clone_names)
    ):
        try:
            online = client.is_connected()
            username = None
            if hasattr(client, "_self_user") and client._self_user:
                username = client._self_user.username

            clones.append({
                "index": i + 1,
                "name": name,
                "online": online,
                "username": username,
            })
        except Exception:
            clones.append({
                "index": i + 1,
                "name": name,
                "online": False,
                "username": None,
            })

    loops = [
        {"chat_id": cid, "text": txt}
        for (cid, txt) in _automation.loops.active.keys()
    ]

    return jsonify({
        "mirror_mode": _automation.mirror_mode,
        "main_online": main_online,
        "main_name": main_name,
        "main_username": main_username,
        "main_id": main_id,
        "clones": clones,
        "loops": loops,
        "total_clones": len(clones),
        "online_clones": sum(1 for c in clones if c["online"]),
        "active_loops": len(loops),
    })






@api_app.route("/logs", methods=["GET"])
@require_token
def api_logs():
    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100

    limit = max(1, min(limit, 1000))
    return jsonify({"logs": list(admin_log_buffer)[-limit:]})


@api_app.route("/logs/clear", methods=["POST"])
@require_token
def api_logs_clear():
    admin_log_buffer.clear()
    return jsonify({"success": True})






@api_app.route("/packages", methods=["GET"])
@require_token
def api_packages():
    return jsonify({"packages": check_packages()})


@api_app.route("/packages/install", methods=["POST"])
@require_token
def api_packages_install():
    pkgs = check_packages()
    missing = [
        name for name, info in pkgs.items()
        if not info["installed"] and name.lower() != "ffmpeg"
    ]

    if not missing:
        return jsonify({"success": True, "message": "All installed"})

    results = []
    for pkg in missing:
        r = install_package(pkg)
        results.append({"package": pkg, **r})

    all_ok = all(r["success"] for r in results)
    return jsonify({
        "success": all_ok,
        "message": "All installed" if all_ok else "Some failed",
        "results": results,
    })






@api_app.route("/sessions/list", methods=["GET"])
@require_token
def api_sessions_list():
    try:
        from session_manager import (
            list_all_mains,
            list_clones,
            load_state,
            get_bridge_invite_link,
        )
        state = load_state()
        return jsonify({
            "active_main": state.get("active_main", "Main"),
            "mains": list_all_mains(),
            "clones": list_clones(),
            "bridge_link": get_bridge_invite_link(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_app.route("/sessions/activate", methods=["POST"])
@require_token
def api_sessions_activate():
    try:
        from session_manager import set_active_main
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"success": False, "message": "Name required"})
        return jsonify(set_active_main(name))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@api_app.route("/sessions/bridge", methods=["POST"])
@require_token
def api_sessions_bridge():
    try:
        from session_manager import set_bridge_invite_link
        data = request.get_json(silent=True) or {}
        link = data.get("link", "").strip()
        return jsonify(set_bridge_invite_link(link))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@api_app.route("/sessions/repair", methods=["POST"])
@require_token
def api_sessions_repair():
    if _automation is None:
        return jsonify({
            "success": False,
            "message": "Automation not available",
        }), 503
    try:
        return jsonify(_automation.repair_sessions_sync())
    except Exception as exc:
        logger.error("[REPAIR] API request failed: %s", exc, exc_info=True)
        return jsonify({"success": False, "message": str(exc)})


@api_app.route("/sessions/convert", methods=["POST"])
@require_token
def api_sessions_convert():
    try:
        from session_manager import convert_session
        data = request.get_json(silent=True) or {}
        source_name = data.get("source_name", "").strip()
        source_type = data.get("source_type", "").strip()
        target_type = data.get("target_type", "").strip()
        if not source_name or not source_type or not target_type:
            return jsonify({"success": False, "message": "All fields required"})
        return jsonify(convert_session(source_name, source_type, target_type))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@api_app.route("/sessions/make/start", methods=["POST"])
@require_token
def api_sessions_make_start():
    try:
        from session_manager import start_session_creation
        data = request.get_json(silent=True) or {}
        session_type = data.get("type", "").strip()
        phone = data.get("phone", "").strip()

        if not phone:
            return jsonify({"success": False, "message": "Phone required"})
        if session_type not in ("main", "clone"):
            return jsonify({"success": False, "message": "Invalid type"})

        result = _run_async(
            start_session_creation(session_type, phone),
            timeout=180,
        )
        if not isinstance(result, dict):
            logger.error(
                "[MAKE] Start returned invalid result: %r",
                result,
            )
            return jsonify({
                "success": False,
                "message": "Session creation returned an empty response",
            })
        if result.get("success") and not result.get("session_id"):
            logger.error(
                "[MAKE] Start succeeded without session_id: %r",
                result,
            )
            return jsonify({
                "success": False,
                "message": "OTP session was not created correctly",
            })
        if result.get("success"):
            result["code_required"] = True
        return jsonify(result)
    except TimeoutError:
        return jsonify({
            "success": False,
            "message": "Session creation start timed out",
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@api_app.route("/sessions/make/complete", methods=["POST"])
@require_token
def api_sessions_make_complete():
    try:
        from session_manager import complete_session_creation
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id", "").strip()
        code = data.get("code", "").strip()
        password = data.get("password")
        setup_2fa_password = data.get("setup_2fa_password")
        setup_2fa_hint = data.get("setup_2fa_hint")

        if not session_id or not code:
            return jsonify({
                "success": False,
                "message": "session_id and code required",
            })

        result = _run_async(
            complete_session_creation(
                session_id,
                code,
                password,
                setup_2fa_password=setup_2fa_password,
                setup_2fa_hint=setup_2fa_hint,
            ),
            timeout=180,
        )
        return jsonify(result)
    except TimeoutError:
        return jsonify({
            "success": False,
            "message": "Session creation completion timed out",
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@api_app.route("/sessions/make/cancel", methods=["POST"])
@require_token
def api_sessions_make_cancel():
    try:
        from session_manager import cancel_session_creation
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id", "").strip()
        if not session_id:
            return jsonify({"success": False, "message": "session_id required"})
        return jsonify(cancel_session_creation(session_id))
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})






@api_app.route("/bot/reload", methods=["POST"])
@require_token
def api_bot_reload():
    if _automation is None:
        return jsonify({"success": False, "message": "Automation not available"})
    try:
        return jsonify(_automation.request_reload())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@api_app.route("/internal/telethon/status", methods=["GET"])
@require_token
def api_internal_telethon_status():
    """Status endpoint reserved for the separate aiogram control plane."""
    if _automation is None:
        return jsonify({"success": False, "message": "Automation unavailable"}), 503
    try:
        return jsonify(_automation.telethon_status())
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@api_app.route("/internal/telethon/start", methods=["POST"])
@require_token
def api_internal_telethon_start():
    if _automation is None:
        return jsonify({"success": False, "message": "Automation unavailable"}), 503
    try:
        return jsonify(_automation.start_telethon_sync())
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@api_app.route("/internal/telethon/stop", methods=["POST"])
@require_token
def api_internal_telethon_stop():
    if _automation is None:
        return jsonify({"success": False, "message": "Automation unavailable"}), 503
    try:
        return jsonify(_automation.stop_telethon_sync())
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500


@api_app.route("/internal/telethon/restart", methods=["POST"])
@require_token
def api_internal_telethon_restart():
    if _automation is None:
        return jsonify({"success": False, "message": "Automation unavailable"}), 503
    try:
        return jsonify(_automation.restart_telethon_sync())
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500






def run_admin_api():
    """Run admin API on localhost only."""
    setup_admin_logging()

    logger.info(
        f"[ADMIN-API] Starting on {ADMIN_API_HOST}:{ADMIN_API_PORT} "
        f"(localhost only)"
    )

    thread = threading.Thread(
        target=lambda: api_app.run(
            host=ADMIN_API_HOST,
            port=ADMIN_API_PORT,
            debug=False,
            use_reloader=False,
        ),
        daemon=True,
        name="AdminAPI",
    )
    thread.start()
    return thread

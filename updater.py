

"""Safe GitHub updater and Telegram update notifier for TelCloneManager."""

__TCM_FILE_HASH__ = "5830427196"

import asyncio
import base64
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
REPOSITORY = os.environ.get(
    "UPDATER_REPOSITORY", "https://github.com/farshid908/TelCloneManager"
).rstrip("/")
GITHUB_API_REPOSITORY = (
    REPOSITORY.replace("https://github.com/", "https://api.github.com/repos/", 1)
)
BRANCH = os.environ.get("UPDATER_BRANCH", "main")
MAIN_SCREEN = os.environ.get("MAIN_SCREEN_NAME", "telegram")
UPDATER_SCREEN = os.environ.get("UPDATER_SCREEN_NAME", "updater")
UPDATE_INTERVAL = max(300, int(os.environ.get("UPDATE_INTERVAL", "86400")))
CHECK_INTERVAL = max(5, int(os.environ.get("UPDATE_CHECK_INTERVAL", "15")))
MAIN_START_COMMAND = os.environ.get(
    "MAIN_START_COMMAND",
    "python3 -u main.py >> runtime.stdout.log 2>> runtime.stderr.log",
)
REQUEST_FILE = ROOT / ".update_request.json"
STATE_FILE = ROOT / ".update_state.json"
PID_FILE = ROOT / ".updater.pid"
BACKUP_ROOT = ROOT / ".update_backups"
ERROR_FILE = ROOT / "update_error.txt"
UPDATE_OPERATION_LOCK = asyncio.Lock()
DELETE_TASKS = set()
LAST_MENU_SENT = {}

try:
    sys.path.insert(0, str(ROOT))
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    from config import ADMIN_API_HOST, ADMIN_API_PORT, ADMIN_API_TOKEN
    from config import CLONE_MANAGER_BOT_TOKEN
except Exception:
    ADMIN_API_HOST = os.environ.get("ADMIN_API_HOST", "127.0.0.1")
    ADMIN_API_PORT = int(os.environ.get("ADMIN_API_PORT", "1600"))
    ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "")
    CLONE_MANAGER_BOT_TOKEN = os.environ.get("CLONE_MANAGER_BOT_TOKEN", "")

ADMIN_API_URL = f"http://{ADMIN_API_HOST}:{ADMIN_API_PORT}"
logger = logging.getLogger("TCM-Updater")


class TelegramBotAPI:
    def __init__(self, token):
        self.base_url = f"https://api.telegram.org/bot{token}"

    def _request(self, method, payload=None, file_path=None, file_name=None):
        payload = payload or {}
        if file_path:
            boundary = "----TelCloneManagerUpdaterBoundary"
            chunks = []
            for key, value in payload.items():
                chunks.extend([
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                    str(value).encode(),
                    b"\r\n",
                ])
            data = Path(file_path).read_bytes()
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="document"; filename="{file_name or Path(file_path).name}"\r\n'.encode(),
                b"Content-Type: text/plain\r\n\r\n",
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ])
            body = b"".join(chunks)
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        else:
            body = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        request = Request(
            f"{self.base_url}/{method}",
            data=body,
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(result.get("description", "Telegram Bot API request failed"))
        return result

    async def send_message(self, chat_id, text, reply_markup=None):
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await asyncio.to_thread(self._request, "sendMessage", payload)

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await asyncio.to_thread(self._request, "editMessageText", payload)

    async def delete_message(self, chat_id, message_id):
        payload = {"chat_id": chat_id, "message_id": message_id}
        return await asyncio.to_thread(self._request, "deleteMessage", payload)

    async def send_document(self, chat_id, document, caption=None):
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption
        return await asyncio.to_thread(
            self._request,
            "sendDocument",
            payload,
            document,
            Path(document).name,
        )

    async def close(self):
        return None


def _load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else default.copy()
    except (OSError, ValueError, TypeError):
        return default.copy()


def _save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _api_call(path, method="GET", payload=None, timeout=20):
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = Request(
        ADMIN_API_URL + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {ADMIN_API_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"error": str(exc)}


def _github_request(path):
    request = Request(
        f"{GITHUB_API_REPOSITORY}/" + path.lstrip("/"),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "TelCloneManager-updater",
        },
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def latest_commit():
    try:
        payload = _github_request(f"commits/{BRANCH}")
        return payload["sha"]
    except Exception as api_error:
        result = subprocess.run(
            [
                "git",
                "ls-remote",
                f"{REPOSITORY}.git",
                f"refs/heads/{BRANCH}",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.split()[0]
        raise RuntimeError(
            f"GitHub commit check failed: {api_error}; "
            f"git fallback failed: {result.stderr.strip()}"
        )


def _remote_manifest():
    payload = _github_request(f"contents/hashforupdate?ref={BRANCH}")
    content = payload.get("content", "")
    if payload.get("encoding") != "base64" or not content:
        raise RuntimeError("GitHub hashforupdate is missing or invalid")
    text = base64.b64decode(content.replace("\n", "")).decode(
        "utf-8", "replace"
    )
    markers = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) == 10 and value.isdigit():
            markers[key.strip()] = value
    if not markers:
        raise RuntimeError("GitHub hashforupdate is empty or invalid")
    normalized = "\n".join(
        line.strip() for line in text.splitlines() if line.strip()
    )
    return markers, normalized


def _marker_path(key):
    if key == "requirements":
        return ROOT / "requirements.txt"
    if key.endswith("____init__"):
        package = key[: -len("____init__")].replace("__", "/")
        return ROOT / package / "__init__.py"
    return ROOT / (key.replace("__", "/") + ".py")


def _local_marker(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if path.name == "requirements.txt":
        pattern = r"^#\s*TCM_REQUIREMENTS_HASH=(\d{10})\s*$"
    else:
        pattern = r"__TCM_FILE_HASH__\s*=\s*[\"'](\d{10})[\"']"
    import re
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1) if match else None


def _marker_mismatches(markers):
    mismatches = []
    for key, expected in markers.items():
        path = _marker_path(key)
        if not path.is_file() or _local_marker(path) != expected:
            mismatches.append(key)
    return mismatches


def _local_manifest_normalized():
    path = ROOT / "hashforupdate"
    try:
        return "\n".join(
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except OSError:
        return None


def _manifest_digest():
    path = ROOT / "hashforupdate"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _save_installed_state(commit, manifest_digest=None):
    state = _load_json(STATE_FILE, {})
    state.update(
        {
            "current_commit": commit,
            "installed_commit": commit,
            "manifest_digest": manifest_digest or _manifest_digest(),
            "updated_at": time.time(),
            "status": "healthy",
        }
    )
    _save_json(STATE_FILE, state)


def save_restart_notice(chat_id, message_id):
    state = _load_json(STATE_FILE, {})
    state["restart_notice"] = {
        "chat_id": int(chat_id),
        "message_id": int(message_id),
    }
    _save_json(STATE_FILE, state)


def consume_restart_notice():
    state = _load_json(STATE_FILE, {})
    notice = state.pop("restart_notice", None)
    if notice is not None:
        _save_json(STATE_FILE, state)
    return notice


def _read_request():
    request = _load_json(REQUEST_FILE, {})
    try:
        REQUEST_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return {}
    return request


def request_update(action, chat_id, message_id):
    _save_json(
        REQUEST_FILE,
        {
            "action": action,
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def is_updater_running():
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _keyboard():
    return {
        "inline_keyboard": [
            [{"text": "Update now", "callback_data": "update:now"}],
            [{"text": "Remind me in 24 hrs", "callback_data": "update:remind"}],
        ]
    }


def _main_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "Clone List 📋", "callback_data": "menu:clone_list"},
                {"text": "Status 📊", "callback_data": "menu:status"},
            ],
            [
                {"text": "Clone Mod 👥", "callback_data": "menu:clone_mode"},
                {"text": "Normal Mod 👤", "callback_data": "menu:normal_main"},
            ],
            [{"text": "🔧Session Settings⚙", "callback_data": "menu:session_settings"}],
            [{"text": "Check for update🔁", "callback_data": "update:check"}],
        ]
    }


async def _send_main_menu(bot, chat_id):
    if not chat_id:
        return None
    now = time.monotonic()
    if now - LAST_MENU_SENT.get(chat_id, 0) < 2:
        logger.info("Skipping duplicate main menu for chat=%s", chat_id)
        return None
    status = _api_call("/status")
    online_clones = status.get("online_clones", 0)
    total_clones = status.get("total_clones", 0)
    mirror_enabled = bool(status.get("mirror_mode", False))
    active_loops = status.get("active_loops", 0)
    try:
        from clone_settings.normal_mode_store import load_active_mode

        active_mode = load_active_mode().title()
    except Exception:
        active_mode = "Clone"
    text = (
        "🤖 Clone Manager — Main Menu\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 Status Overview:\n"
        f"  • Clones: {online_clones}/{total_clones} online\n"
        f"  • Mirror: {'ON ✅' if mirror_enabled else 'OFF ❌'}\n"
        f"  • Active Loops: {active_loops}\n\n"
        f"  • Active Mod: {active_mode}\n\n"
        "Select a section below:"
    )
    result = await bot.send_message(
        chat_id,
        text,
        reply_markup=_main_menu_keyboard(),
    )
    LAST_MENU_SENT[chat_id] = now
    return result


async def _delete_after_delay(bot, chat_id, message_id, delay=60):
    await asyncio.sleep(delay)
    for attempt in range(3):
        try:
            await bot.delete_message(chat_id, message_id)
            return
        except Exception as exc:
            if attempt == 2:
                logger.warning(
                    "Could not delete update status message chat=%s message=%s: %s",
                    chat_id,
                    message_id,
                    exc,
                )
                return
            await asyncio.sleep(2)


def _schedule_message_deletion(bot, chat_id, message_id, delay=60):
    if not chat_id or not message_id:
        return
    task = asyncio.create_task(
        _delete_after_delay(bot, chat_id, message_id, delay)
    )
    DELETE_TASKS.add(task)
    task.add_done_callback(DELETE_TASKS.discard)


def _message_id(result, fallback=None):
    if not isinstance(result, dict):
        return fallback
    payload = result.get("result")
    if isinstance(payload, dict):
        return payload.get("message_id", fallback)
    return fallback


async def _show_reminder_and_menu(bot, chat_id, message_id):
    if not chat_id:
        return
    reminder_id = message_id
    if not reminder_id:
        result = await bot.send_message(
            chat_id,
            "Okay. I will remind you in 24 hours.",
            reply_markup=None,
        )
        reminder_id = result.get("result", {}).get("message_id")
    await _send_main_menu(bot, chat_id)
    _schedule_message_deletion(bot, chat_id, reminder_id, 60)


async def _edit_or_send(bot, chat_id, message_id, text, keyboard=None):
    if message_id:
        try:
            return await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
            )
        except Exception as exc:
            logger.info("Could not edit update message: %s", exc)
    return await bot.send_message(chat_id, text, reply_markup=keyboard)


async def _show_already_up_to_date(bot, request):
    chat_id = request.get("chat_id")
    if not chat_id:
        return
    result = await _edit_or_send(
        bot,
        chat_id,
        request.get("message_id"),
        "Already up to date.",
        None,
    )
    status_message_id = _message_id(result, request.get("message_id"))
    await _send_main_menu(bot, chat_id)
    _schedule_message_deletion(bot, chat_id, status_message_id, 60)


def _status_admin_id():
    response = _api_call("/status")
    return response.get("main_id")


async def show_update_available(bot, chat_id=None, message_id=None):
    chat_id = chat_id or _status_admin_id()
    if not chat_id:
        return None
    message = await _edit_or_send(
        bot,
        chat_id,
        message_id,
        "A new TelCloneManager update is available.",
        _keyboard(),
    )
    return message.get("result", {}).get("message_id", message_id)


def _source_files(directory):
    result = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if path.suffix == ".py" or path.name == "requirements.txt":
            result.append(relative)
    return result


def _compile_tree(directory, files):
    for relative in files:
        if relative.suffix != ".py":
            continue
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(directory / relative)],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(
                f"Syntax check failed for {relative}:\n"
                f"{result.stdout}\n{result.stderr}"
            )


def _download_source():
    request = Request(
        f"{REPOSITORY}/archive/refs/heads/{BRANCH}.zip",
        headers={"User-Agent": "TelCloneManager-updater"},
    )
    temporary = Path(tempfile.mkdtemp(prefix="tcm-update-"))
    archive = temporary / "source.zip"
    with urlopen(request, timeout=180) as response, archive.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    with zipfile.ZipFile(archive) as compressed:
        compressed.extractall(temporary / "extract")
    roots = [path for path in (temporary / "extract").iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("GitHub archive has an unexpected directory layout")
    return temporary, roots[0]


def _backup_and_install(source):
    files = _source_files(source)
    _compile_tree(source, files)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUP_ROOT / stamp
    backup.mkdir(parents=True, exist_ok=False)
    existing = []
    created = []
    for relative in files:
        destination = ROOT / relative
        backup_path = backup / relative
        if destination.exists():
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup_path)
            existing.append(relative)
        else:
            created.append(relative)
    try:
        for relative in files:
            destination = ROOT / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
    except Exception:
        for relative in existing:
            shutil.copy2(backup / relative, ROOT / relative)
        for relative in created:
            try:
                (ROOT / relative).unlink()
            except FileNotFoundError:
                pass
        raise
    return backup, files


def _restore(backup, files):
    for relative in files:
        saved = backup / relative
        destination = ROOT / relative
        if saved.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, destination)


def _stop_main():
    response = _api_call("/bot/shutdown", method="POST", timeout=10)
    if response.get("error"):
        raise RuntimeError(f"Could not stop main process: {response['error']}")
    time.sleep(2)
    subprocess.run(
        ["screen", "-S", MAIN_SCREEN, "-X", "quit"],
        capture_output=True,
        text=True,
    )
    time.sleep(2)


def _start_main():
    result = subprocess.run(
        [
            "screen",
            "-dmS",
            MAIN_SCREEN,
            "bash",
            "-lc",
            f"cd {ROOT!s} && exec {MAIN_START_COMMAND}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or "Could not start main screen")


async def _send_error(bot, chat_id, error_text):
    ERROR_FILE.write_text(error_text, encoding="utf-8")
    try:
        await bot.send_document(
            chat_id,
            ERROR_FILE,
            caption="The update failed. The previous source backup was restored.",
        )
    except Exception as exc:
        logger.error("Could not send error report: %s", exc)


def _dependency_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "update now❇️", "callback_data": "update:deps_now"}],
            [{"text": "remind me 24hrs💤", "callback_data": "update:deps_remind"}],
        ]
    }


async def _run_update_script(bot, request):
    chat_id = request.get("chat_id") or _status_admin_id()
    status_message_id = request.get("message_id")
    target_commit = latest_commit()
    if chat_id:
        result = await _edit_or_send(
            bot,
            chat_id,
            status_message_id,
            "Updating TelCloneManager...",
            None,
        )
        status_message_id = _message_id(result, status_message_id)
    progress_file = ROOT / ".update_progress.log"
    dependency_log = ROOT / "dependency_update_error.txt"
    try:
        update_env = os.environ.copy()
        update_env["TCM_TARGET_COMMIT"] = target_commit
        process = await asyncio.create_subprocess_exec(
            "bash",
            str(ROOT / "update.sh"),
            cwd=str(ROOT),
            env=update_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        last_line = ""
        next_edit = 0.0
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            last_line = raw.decode("utf-8", "replace").strip()
            now = time.monotonic()
            if chat_id and last_line and now >= next_edit:
                result = await _edit_or_send(
                    bot,
                    chat_id,
                    status_message_id,
                    f"Updating TelCloneManager...\n{last_line}",
                    None,
                )
                status_message_id = _message_id(result, status_message_id)
                next_edit = now + 2.0
        return_code = await process.wait()
        if return_code == 0:
            if chat_id:
                result = await _edit_or_send(
                    bot,
                    chat_id,
                    status_message_id,
                    "Update completed successfully. The bot is restarting...",
                    None,
                )
                status_message_id = _message_id(result, status_message_id)
                if status_message_id:
                    save_restart_notice(chat_id, status_message_id)
            _save_installed_state(target_commit)
            return
        if return_code == 42:
            if chat_id and dependency_log.is_file():
                await bot.send_document(
                    chat_id,
                    dependency_log,
                    caption=(
                        "Some requirements are not compatible with the active Python "
                        "interpreter. The new source was downloaded but not started."
                    ),
                )
            if chat_id:
                result = await _edit_or_send(
                    bot,
                    chat_id,
                    status_message_id,
                    "Dependency update is required before the source can start.\n"
                    "This may take 15-30 minutes depending on the system.",
                    _dependency_keyboard(),
                )
                status_message_id = _message_id(result, status_message_id)
            return
        error_text = (
            f"Update script failed with exit code {return_code}.\n"
            f"Last log line: {last_line or 'no output'}\n"
        )
        if progress_file.is_file():
            error_text += "\n" + progress_file.read_text(encoding="utf-8", errors="replace")
        if chat_id:
            await _send_error(bot, chat_id, error_text)
            result = await _edit_or_send(
                bot,
                chat_id,
                status_message_id,
                "⚠️ Update failed. See the log file.",
                None,
            )
            status_message_id = _message_id(result, status_message_id)
            _schedule_message_deletion(bot, chat_id, status_message_id, 60)
    except Exception as exc:
        logger.error("Update script execution failed: %s", exc, exc_info=True)
        if chat_id:
            await _send_error(bot, chat_id, f"Update script execution failed: {type(exc).__name__}: {exc}")


async def perform_update(bot, request):
    if UPDATE_OPERATION_LOCK.locked():
        chat_id = request.get("chat_id")
        if chat_id:
            await _edit_or_send(
                bot,
                chat_id,
                request.get("message_id"),
                "An update is already in progress.",
                None,
            )
        return
    async with UPDATE_OPERATION_LOCK:
        await _run_update_script(bot, request)


async def _handle_request(bot, request):
    action = request.get("action")
    if action == "update":
        await perform_update(bot, request)
        return
    if action == "remind":
        state = _load_json(STATE_FILE, {})
        state["remind_until"] = time.time() + 86400
        _save_json(STATE_FILE, state)
        if request.get("chat_id"):
            await _show_reminder_and_menu(
                bot, request["chat_id"], request.get("message_id")
            )
        return
    if action == "check":
        await check_for_update(bot, request)
    if action == "deps_now":
        await perform_update(bot, request)
        return
    if action == "deps_remind":
        if request.get("chat_id"):
            await _show_reminder_and_menu(
                bot, request["chat_id"], request.get("message_id")
            )
        return


async def check_for_update(bot, request=None):
    request = request or {}
    try:
        remote = latest_commit()
        remote_markers, remote_manifest = _remote_manifest()
        marker_mismatches = _marker_mismatches(remote_markers)
        manifest_matches = _local_manifest_normalized() == remote_manifest
        state = _load_json(STATE_FILE, {})
        current = state.get("current_commit")
        source_is_current = not marker_mismatches and manifest_matches
        if source_is_current and (not current or current != remote):
            _save_installed_state(remote)
            current = remote
        if source_is_current and remote == current:
            await _show_already_up_to_date(bot, request)
            return False
        if source_is_current and not current:
            _save_installed_state(remote)
            await _show_already_up_to_date(bot, request)
            return False
        if remote == current and not marker_mismatches and manifest_matches:
            await _show_already_up_to_date(bot, request)
            return False
        remind_until = float(state.get("remind_until", 0) or 0)
        if request.get("action") == "check" or time.time() >= remind_until:
            message_id = await show_update_available(
                bot, request.get("chat_id"), request.get("message_id")
            )
            state["notice_message_id"] = message_id
            state["notice_chat_id"] = request.get("chat_id") or _status_admin_id()
            _save_json(STATE_FILE, state)
        return True
    except Exception as exc:
        logger.error("Update check failed: %s", exc, exc_info=True)
        if request.get("chat_id"):
            await _edit_or_send(
                bot,
                request["chat_id"],
                request.get("message_id"),
                "⚠️ Update check failed. Please try again later.",
                None,
            )
        return False


async def run():
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    bot = TelegramBotAPI(CLONE_MANAGER_BOT_TOKEN)
    try:
        state = _load_json(STATE_FILE, {})
        await check_for_update(bot)
        next_check = time.monotonic() + UPDATE_INTERVAL
        while True:
            request = _read_request()
            if request:
                await _handle_request(bot, request)
                next_check = time.monotonic() + UPDATE_INTERVAL
            if time.monotonic() >= next_check:
                await check_for_update(bot)
                next_check = time.monotonic() + UPDATE_INTERVAL
            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await bot.close()
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass

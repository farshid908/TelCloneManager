"""Private localhost API used by the aiogram manager to control Telethon."""

from __future__ import annotations

__TCM_FILE_HASH__ = "8060742888"


import json
import urllib.error
import urllib.request

from config import ADMIN_API_HOST, ADMIN_API_PORT, ADMIN_API_TOKEN


def _request(path: str, method: str = "GET", payload: dict | None = None):
    url = f"http://{ADMIN_API_HOST}:{ADMIN_API_PORT}{path}"
    body = None
    headers = {"Authorization": f"Bearer {ADMIN_API_TOKEN}"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {"success": False, "message": f"Internal API unavailable: {exc}"}
    except Exception as exc:
        return {"success": False, "message": str(exc)}


def telethon_status():
    return _request("/internal/telethon/status")


def telethon_start():
    return _request("/internal/telethon/start", method="POST")


def telethon_stop():
    return _request("/internal/telethon/stop", method="POST")


def telethon_restart():
    return _request("/internal/telethon/restart", method="POST")


def source_update(payload: dict):
    return _request("/internal/source/update", method="POST", payload=payload)

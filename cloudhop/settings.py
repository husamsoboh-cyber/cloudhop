"""CloudHop application settings.

Saves and loads user-configurable settings (e.g. email notifications).
Persistence: ``~/.cloudhop/settings.json`` (thread-safe via ``threading.Lock``).
"""

import json
import logging
import os
import threading
from typing import Any, Dict

from .utils import _CM_DIR

logger = logging.getLogger("cloudhop.settings")

_SETTINGS_FILE = os.path.join(_CM_DIR, "settings.json")
_lock = threading.Lock()


def _default_settings() -> Dict[str, Any]:
    """Return the default settings dictionary."""
    return {
        "email_enabled": False,
        "email_smtp_host": "",
        "email_smtp_port": 587,
        "email_smtp_tls": True,
        "email_from": "",
        "email_to": "",
        "email_username": "",
        "email_password": "",
        "email_on_complete": True,
        "email_on_failure": True,
    }


def _load() -> Dict[str, Any]:
    """Load raw settings from disk, merged with defaults."""
    defaults = _default_settings()
    if not os.path.exists(_SETTINGS_FILE):
        return defaults
    try:
        with open(_SETTINGS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            defaults.update(data)
            return defaults
        logger.warning("settings.json is not a dict, resetting")
        return _default_settings()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("settings.json corrupt (%s), resetting", exc)
        return _default_settings()


def _save(settings: Dict[str, Any]) -> None:
    """Write settings to disk atomically."""
    tmp = _SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, _SETTINGS_FILE)


def load_settings() -> Dict[str, Any]:
    """Load settings with password redacted. Safe for API responses."""
    with _lock:
        settings = _load()
    settings["email_password"] = ""
    logger.debug("Settings loaded")
    return settings


def load_settings_with_secrets() -> Dict[str, Any]:
    """Load settings including the real password. For internal use only."""
    with _lock:
        settings = _load()
    logger.debug("Settings loaded (with secrets)")
    return settings


def save_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and save settings. Returns {"ok": True} or {"ok": False, "msg": "..."}."""
    # Validate email_smtp_port
    try:
        port = int(data.get("email_smtp_port", 587))
    except (ValueError, TypeError):
        return {"ok": False, "msg": "SMTP port must be a number"}
    if port < 1 or port > 65535:
        return {"ok": False, "msg": "SMTP port must be between 1 and 65535"}

    # Validate email_smtp_host
    host = str(data.get("email_smtp_host", ""))
    if len(host) > 255:
        return {"ok": False, "msg": "SMTP host too long (max 255 chars)"}

    # Validate email addresses
    for field in ("email_from", "email_to"):
        val = str(data.get(field, ""))
        if val and ("@" not in val or "." not in val):
            return {"ok": False, "msg": f"Invalid email address for {field}"}

    with _lock:
        existing = _load()

        # Preserve existing password if incoming password is empty
        if not data.get("email_password"):
            data["email_password"] = existing.get("email_password", "")

        # Merge with defaults, then apply incoming data
        defaults = _default_settings()
        merged = {**defaults, **existing, **data}
        # Ensure only known keys are saved
        settings = {k: merged[k] for k in defaults}
        # Coerce port to int
        settings["email_smtp_port"] = port

        _save(settings)

    logger.info("Settings saved")
    return {"ok": True}

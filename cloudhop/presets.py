"""CloudHop transfer presets.

Saves, loads, and runs reusable transfer configurations.
Persistence: ``~/.cloudhop/presets.json`` (thread-safe via ``threading.Lock``).
"""

import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .utils import _CM_DIR

logger = logging.getLogger("cloudhop.presets")

_PRESETS_FILE = os.path.join(_CM_DIR, "presets.json")
_lock = threading.Lock()


def _load() -> List[Dict[str, Any]]:
    """Load presets from disk. Returns [] on missing/corrupt file."""
    if not os.path.exists(_PRESETS_FILE):
        return []
    try:
        with open(_PRESETS_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("presets.json is not a list, resetting")
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("presets.json corrupt (%s), resetting", exc)
        return []


def _save(presets: List[Dict[str, Any]]) -> None:
    """Write presets to disk atomically."""
    tmp = _PRESETS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(presets, f, indent=2)
    os.replace(tmp, _PRESETS_FILE)


def save_preset(name: str, config: Dict[str, Any]) -> str:
    """Save a new preset. Returns the preset_id."""
    preset_id = secrets.token_hex(8)
    preset = {
        "preset_id": preset_id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used": None,
        "use_count": 0,
        "config": config,
    }
    with _lock:
        presets = _load()
        presets.append(preset)
        _save(presets)
    logger.info("Preset saved: %s (%s)", name, preset_id)
    return preset_id


def list_presets() -> List[Dict[str, Any]]:
    """Return all presets."""
    with _lock:
        presets = _load()
    logger.debug("Listed %d presets", len(presets))
    return presets


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    """Return a single preset by ID, or None."""
    with _lock:
        presets = _load()
    for p in presets:
        if p.get("preset_id") == preset_id:
            logger.debug("Got preset %s", preset_id)
            return p
    return None


def delete_preset(preset_id: str) -> bool:
    """Delete a preset. Returns True if found and deleted."""
    with _lock:
        presets = _load()
        before = len(presets)
        presets = [p for p in presets if p.get("preset_id") != preset_id]
        if len(presets) == before:
            return False
        _save(presets)
    logger.info("Preset deleted: %s", preset_id)
    return True


def run_preset(preset_id: str, manager: Any) -> Dict[str, Any]:
    """Run a preset: start a transfer with the saved config. Returns the transfer result."""
    with _lock:
        presets = _load()
        preset = None
        for p in presets:
            if p.get("preset_id") == preset_id:
                preset = p
                break
        if preset is None:
            return {"ok": False, "msg": "Preset not found"}
        preset["last_used"] = datetime.now(timezone.utc).isoformat()
        preset["use_count"] = preset.get("use_count", 0) + 1
        _save(presets)

    config = dict(preset["config"])
    logger.info("Running preset: %s (%s)", preset["name"], preset_id)
    result = manager.start_transfer(config)
    result["preset_id"] = preset_id
    return result

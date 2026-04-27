"""UI settings persistence.

Settings split into two buckets:
  - system fields  -> mirrored to MQTT (turret/hub/config retained) so other
                      services pick them up. Keys: motion_timeout_s,
                      trailing_s, target_class.
  - ui-only fields -> just live in the JSON file. Keys: default_control_scheme,
                      telemetry_poll_interval_s.

File: ~/.local/share/sonofpawd/ui_settings.json. No sudo needed; the
ui-backend systemd unit runs as the user.
"""
import json
import logging
import os
from threading import Lock
from typing import Any, Dict

log = logging.getLogger("ui.settings")

_SETTINGS_DIR = os.path.expanduser("~/.local/share/sonofpawd")
_SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "ui_settings.json")

# Authoritative defaults. UI Settings page falls back to these when the file
# doesn't exist yet, and they are also the values the hub publishes on
# startup if no config has been seen.
DEFAULTS: Dict[str, Any] = {
    "motion_timeout_s": 2,
    "trailing_s": 10,
    "target_class": "cat",
    "default_control_scheme": "joystick",
    "telemetry_poll_interval_s": 5,
}

VALID_TARGET_CLASSES = {"cat", "raccoon", "rat", "deer"}
VALID_SCHEMES = {"joystick", "keyboard", "tilt"}

# Fields that get published to turret/hub/config retained.
SYSTEM_FIELDS = {"motion_timeout_s", "trailing_s", "target_class"}

_lock = Lock()


def load() -> Dict[str, Any]:
    with _lock:
        try:
            with open(_SETTINGS_PATH) as f:
                stored = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            stored = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
    return merged


def save(values: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, merge over defaults, persist, return the effective config."""
    merged = dict(load())
    for k, v in values.items():
        if k not in DEFAULTS:
            continue
        merged[k] = v
    _validate(merged)

    with _lock:
        os.makedirs(_SETTINGS_DIR, exist_ok=True)
        tmp = _SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(merged, f, indent=2, sort_keys=True)
        os.replace(tmp, _SETTINGS_PATH)
    log.info("settings written: %s", merged)
    return merged


def system_subset(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {k: settings[k] for k in SYSTEM_FIELDS if k in settings}


def _validate(s: Dict[str, Any]) -> None:
    if not (1 <= int(s["motion_timeout_s"]) <= 600):
        raise ValueError("motion_timeout_s out of range [1..600]")
    if not (1 <= int(s["trailing_s"]) <= 300):
        raise ValueError("trailing_s out of range [1..300]")
    if s["target_class"] not in VALID_TARGET_CLASSES:
        raise ValueError(f"target_class must be one of {sorted(VALID_TARGET_CLASSES)}")
    if s["default_control_scheme"] not in VALID_SCHEMES:
        raise ValueError(f"default_control_scheme must be one of {sorted(VALID_SCHEMES)}")
    if not (1 <= int(s["telemetry_poll_interval_s"]) <= 60):
        raise ValueError("telemetry_poll_interval_s out of range [1..60]")
    s["motion_timeout_s"] = int(s["motion_timeout_s"])
    s["trailing_s"] = int(s["trailing_s"])
    s["telemetry_poll_interval_s"] = int(s["telemetry_poll_interval_s"])

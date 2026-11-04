"""journalctl wrapper. Whitelisted services only — never feed the URL slug
straight into a shell.

Most services run via `systemctl --user` (queried by _SYSTEMD_USER_UNIT, see
below). Mosquitto and camera-pipeline are system units so they get `-u <name>`.
"""
import logging
import subprocess
from typing import Dict, List

log = logging.getLogger("ui.logs")

# kind ∈ {"user", "system"}.
_SERVICES: Dict[str, str] = {
    "recorder":         "user",
    "hub":              "user",
    "ui-backend":       "user",
    "inference":        "user",
    "mediamtx":         "user",
    "mosquitto":        "system",
    "camera-pipeline":  "system",
    "sensor-wakeup":    "user",
    "ir-led":           "user",
    "fire-control":     "user",
    "motion-ctrl":      "user",
}

KNOWN_SERVICES = sorted(_SERVICES.keys())


def fetch(service: str, lines: int) -> Dict[str, object]:
    if service not in _SERVICES:
        raise ValueError(f"unknown service: {service}")
    lines = max(1, min(int(lines), 1000))
    kind = _SERVICES[service]

    if kind == "user":
        # On this Nano, the per-user journal isn't persisted (systemd-journald
        # default), so `journalctl --user` returns "No journal files were
        # found." The same logs land in the *system* journal tagged with
        # _SYSTEMD_USER_UNIT, which is queryable without --user.
        cmd: List[str] = [
            "journalctl",
            f"_SYSTEMD_USER_UNIT={service}.service",
            "-n", str(lines), "--no-pager",
        ]
    elif kind == "system":
        cmd = ["journalctl", "-u", service, "-n", str(lines), "--no-pager"]
    else:
        raise ValueError(f"unhandled kind: {kind}")

    log.debug("running: %s", " ".join(cmd))
    try:
        # Py3.6 on L4T doesn't have capture_output; use the stdout/stderr pair.
        out = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"service": service, "kind": kind, "error": "timeout", "text": ""}

    text = out.stdout
    if out.returncode != 0 and not text:
        text = out.stderr or f"(journalctl exit {out.returncode})"
    return {
        "service": service,
        "kind": kind,
        "lines": lines,
        "text": text,
    }

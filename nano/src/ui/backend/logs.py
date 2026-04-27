"""journalctl wrapper. Whitelisted services only — never feed the URL slug
straight into a shell.

Nano-side services run via `systemctl --user`, so we use `--user-unit=`.
Mosquitto is a system unit so it gets `-u <name>`. Pi-side services are
fetched over SSH (the deploy Makefile already uses the same login).
"""
import logging
import subprocess
from typing import Dict, List

from common.config.hosts import PI_HOST

log = logging.getLogger("ui.logs")

PI_SSH_USER = "nathan"

# Each entry is (location, kind). kind ∈ {"user", "system", "pi"}.
_SERVICES: Dict[str, str] = {
    "recorder":         "user",
    "hub":              "user",
    "ui-backend":       "user",
    "mediamtx":         "user",
    "mosquitto":        "system",
    "camera-pipeline":  "pi",
    "sensor-wakeup":    "pi",
    "ir-led":           "pi",
    "fire-control":     "pi",
    "motion-ctrl":      "pi",
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
    elif kind == "pi":
        # Pi services are systemctl --user units run as `nathan`. The remote
        # call is non-interactive: BatchMode prevents a hanging password prompt
        # if SSH keys aren't set up.
        remote = f"journalctl --user --user-unit={service} -n {lines} --no-pager"
        cmd = [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4",
            f"{PI_SSH_USER}@{PI_HOST}", remote,
        ]
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

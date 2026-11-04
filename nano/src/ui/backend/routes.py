"""HTTP API endpoints. Thin translator from JSON/REST to MQTT publishes.

Separated from app.py so the Flask factory stays focused on wiring. Routes
call methods on the MqttBridge passed in by main.py.
"""
import logging
import time

from flask import Flask, jsonify, request

from common.messaging.topics import (
    TOPIC_FIRE_ARMED,
    TOPIC_FIRE_ENABLE,
    TOPIC_FIRE_REQUEST,
    TOPIC_HUB_CONFIG,
    TOPIC_MODE_CURRENT,
    TOPIC_MODE_SET,
    TOPIC_MOTION_CTRL_AIM,
    TOPIC_MOTION_CTRL_CALIBRATION,
    TOPIC_MOTION_CTRL_ENABLE,
    TOPIC_RECORDER_ARM,
)

_MANUAL_RECORD_MODES = {"auto-aim", "auto-sentry"}

# Bounds mirror pi/src/motion_ctrl/calibration.py so we can reject bad PUTs
# without round-tripping to the Pi (which may be offline).
_US_ABS_MIN = 500
_US_ABS_MAX = 2500
_SLEW_MIN = 1
_SLEW_MAX = 500


def _validate_axis(d, name):
    if not isinstance(d, dict):
        raise ValueError(f"{name}: expected object")
    try:
        lo = int(d["min_us"]); hi = int(d["max_us"]); ce = int(d["center_us"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{name}: missing/invalid min_us/max_us/center_us")
    if not (_US_ABS_MIN <= lo < ce < hi <= _US_ABS_MAX):
        raise ValueError(
            f"{name}: require {_US_ABS_MIN} <= min_us < center_us < max_us <= {_US_ABS_MAX}"
        )
    return {"min_us": lo, "max_us": hi, "center_us": ce, "invert": bool(d.get("invert", False))}


def _validate_calibration(payload):
    if not isinstance(payload, dict):
        raise ValueError("body must be an object")
    if "pan" not in payload or "tilt" not in payload:
        raise ValueError("missing pan or tilt")
    pan = _validate_axis(payload["pan"], "pan")
    tilt = _validate_axis(payload["tilt"], "tilt")
    try:
        slew_us = int(payload.get("max_slew_us_per_tick", 40))
    except (TypeError, ValueError):
        raise ValueError("max_slew_us_per_tick: not an int")
    if not (_SLEW_MIN <= slew_us <= _SLEW_MAX):
        raise ValueError(f"max_slew_us_per_tick: require {_SLEW_MIN}..{_SLEW_MAX}")
    return {
        "pan": pan,
        "tilt": tilt,
        "max_slew_us_per_tick": slew_us,
    }

from nano.src.hub.modes import VALID_MODES
from nano.src.ui.backend import clips as clips_mod
from nano.src.ui.backend import logs as logs_mod
from nano.src.ui.backend import settings as settings_mod
from nano.src.ui.backend.mqtt_bridge import MqttBridge

log = logging.getLogger("ui.routes")

# Server-side fire rate limit. Defense-in-depth on top of the client-side
# 500 ms cooldown — the FireButton enforces UX, this enforces safety.
_MIN_FIRE_INTERVAL_S = 0.4


def register_routes(app: Flask, bridge: MqttBridge) -> None:

    last_fire_ts = {"v": 0.0}

    @app.post("/api/mode")
    def set_mode():
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
        if mode not in VALID_MODES:
            return jsonify({
                "error": "invalid_mode",
                "received": mode,
                "valid": sorted(VALID_MODES),
            }), 400

        bridge.publish(TOPIC_MODE_SET, {"mode": mode})
        log.info("published mode/set -> %s", mode)
        return jsonify({"ok": True, "requested": mode})

    @app.post("/api/arm")
    def set_arm():
        body = request.get_json(silent=True) or {}
        on = bool(body.get("on", False))
        bridge.publish(TOPIC_FIRE_ARMED, {"on": on}, retain=True)
        log.info("published fire/armed -> %s", on)
        return jsonify({"ok": True, "armed": on})

    @app.post("/api/fire")
    def fire():
        armed = bool(bridge.get_latest(TOPIC_FIRE_ARMED).get("on", False))
        auto_enabled = bool(bridge.get_latest(TOPIC_FIRE_ENABLE).get("on", False))
        if not (armed or auto_enabled):
            return jsonify({"error": "not_armed"}), 409

        now = time.monotonic()
        if now - last_fire_ts["v"] < _MIN_FIRE_INTERVAL_S:
            return jsonify({"error": "rate_limited"}), 429
        last_fire_ts["v"] = now

        bridge.publish(TOPIC_FIRE_REQUEST, {"source": "ui"})
        log.info("published fire/request (armed=%s auto=%s)", armed, auto_enabled)
        return jsonify({"ok": True})

    @app.get("/api/motion-ctrl/calibration")
    def get_calibration():
        cal = bridge.get_latest(TOPIC_MOTION_CTRL_CALIBRATION)
        # Empty dict = motion-ctrl hasn't published yet. Tell the UI so it
        # can render its own defaults rather than guessing.
        return jsonify({"calibration": cal, "present": bool(cal)})

    @app.put("/api/motion-ctrl/calibration")
    def put_calibration():
        body = request.get_json(silent=True) or {}
        try:
            normalized = _validate_calibration(body)
        except ValueError as e:
            return jsonify({"error": "invalid_calibration", "message": str(e)}), 400
        bridge.publish(TOPIC_MOTION_CTRL_CALIBRATION, normalized, retain=True)
        log.info("published motion_ctrl/calibration -> %s", normalized)
        return jsonify(normalized)

    @app.post("/api/recorder/arm")
    def set_recorder_arm():
        body = request.get_json(silent=True) or {}
        on = bool(body.get("on", False))
        mode = bridge.get_latest(TOPIC_MODE_CURRENT).get("mode")
        if mode not in _MANUAL_RECORD_MODES:
            return jsonify({
                "error": "wrong_mode",
                "mode": mode,
                "valid": sorted(_MANUAL_RECORD_MODES),
            }), 409
        # Manual stop = stop now (trailing_s=0). Manual start: trailing_s is
        # only consulted on disarm, so the value is moot for armed=true.
        bridge.publish(TOPIC_RECORDER_ARM, {"armed": on, "trailing_s": 0})
        log.info("published recorder/arm -> %s (manual, mode=%s)", on, mode)
        return jsonify({"ok": True, "armed": on})

    @app.post("/api/motion-ctrl/enable")
    def set_motion_enable():
        body = request.get_json(silent=True) or {}
        enabled = bool(body.get("enabled", False))
        bridge.publish(TOPIC_MOTION_CTRL_ENABLE, {"enabled": enabled}, retain=True)
        log.info("published motion_ctrl/enable -> %s", enabled)
        return jsonify({"ok": True, "enabled": enabled})

    @app.post("/api/aim")
    def aim():
        body = request.get_json(silent=True) or {}
        try:
            pan = float(body.get("pan", 0.0))
            tilt = float(body.get("tilt", 0.0))
        except (TypeError, ValueError):
            return jsonify({"error": "bad_aim"}), 400
        pan = max(-1.0, min(1.0, pan))
        tilt = max(-1.0, min(1.0, tilt))
        bridge.publish(TOPIC_MOTION_CTRL_AIM, {"pan": pan, "tilt": tilt})
        return jsonify({"ok": True})

    # ---- settings -------------------------------------------------------

    @app.get("/api/settings")
    def get_settings():
        return jsonify(settings_mod.load())

    @app.put("/api/settings")
    def put_settings():
        body = request.get_json(silent=True) or {}
        try:
            effective = settings_mod.save(body)
        except ValueError as e:
            return jsonify({"error": "invalid", "message": str(e)}), 400

        # Publish only the system fields so the hub (and eventually the Pi
        # sensor-wakeup) can hot-apply. UI-only fields stay in the JSON file.
        bridge.publish(
            TOPIC_HUB_CONFIG,
            settings_mod.system_subset(effective),
            retain=True,
        )
        log.info("settings updated -> %s", effective)
        return jsonify(effective)

    # ---- logs ----------------------------------------------------------

    @app.get("/api/logs/<service>")
    def get_logs(service: str):
        if service not in logs_mod.KNOWN_SERVICES:
            return jsonify({
                "error": "unknown_service",
                "service": service,
                "valid": logs_mod.KNOWN_SERVICES,
            }), 400
        try:
            lines = int(request.args.get("lines", 200))
        except ValueError:
            lines = 200
        return jsonify(logs_mod.fetch(service, lines))

    @app.get("/api/logs")
    def list_log_services():
        return jsonify({"services": logs_mod.KNOWN_SERVICES})

    # ---- clips ----------------------------------------------------------

    @app.get("/api/clips")
    def get_clips():
        clips = clips_mod.list_clips()
        total = sum(c["size_bytes"] or 0 for c in clips)
        return jsonify({"clips": clips, "total_bytes": total})

    @app.get("/api/clips/<name>")
    def get_clip(name: str):
        from flask import send_file
        mp4 = clips_mod.clip_path(name)
        if mp4 is not None:
            return send_file(mp4, mimetype="video/mp4", conditional=True)
        # Check if the mkv exists but mp4 isn't ready yet
        from pathlib import Path
        if not clips_mod._valid_name(name):
            return jsonify({"error": "invalid_name"}), 404
        mkv = (clips_mod.CLIPS_DIR / Path(name).with_suffix(".mkv").name).resolve()
        if mkv.exists():
            clips_mod.remux_async(mkv)
            return jsonify({"status": "processing"}), 202
        return jsonify({"error": "not_found"}), 404

    @app.delete("/api/clips/<name>")
    def delete_clip_single(name: str):
        if clips_mod.is_remuxing(name):
            return jsonify({"error": "remux_in_progress"}), 409
        ok, reason = clips_mod.delete_clip(name)
        if not ok:
            if "traversal" in reason or "invalid" in reason:
                return jsonify({"error": reason}), 400
            if "remux" in reason:
                return jsonify({"error": reason}), 409
            return jsonify({"error": reason}), 404
        bridge.socketio.emit("clip_changed", {})
        return "", 204

    @app.post("/api/clips/delete")
    def bulk_delete_clips():
        body = request.get_json(silent=True) or {}
        names = body.get("names", [])
        if not isinstance(names, list):
            return jsonify({"error": "names must be a list"}), 400
        # Validate all names before deleting any
        for n in names:
            if not clips_mod._valid_name(n):
                return jsonify({"error": f"invalid name: {n}"}), 400
        deleted = []
        failed = []
        for n in names:
            ok, reason = clips_mod.delete_clip(n)
            if ok:
                deleted.append(n)
            else:
                failed.append({"name": n, "reason": reason})
        bridge.socketio.emit("clip_changed", {})
        return jsonify({"deleted": deleted, "failed": failed})

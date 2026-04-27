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
    TOPIC_MODE_SET,
    TOPIC_MOTION_CTRL_AIM,
    TOPIC_MOTION_CTRL_ENABLE,
)

from nano.src.hub.modes import VALID_MODES
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

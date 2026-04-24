"""HTTP API endpoints. Thin translator from JSON/REST to MQTT publishes.

Separated from app.py so the Flask factory stays focused on wiring. Routes
call methods on the MqttBridge passed in by main.py.

H.4 adds POST /api/mode. Later H-steps add /api/ir, /api/fire, /api/aim,
/api/settings, /api/logs.
"""
import logging

from flask import Flask, jsonify, request

from common.messaging.topics import TOPIC_MODE_SET

from nano.src.hub.modes import VALID_MODES
from nano.src.ui.backend.mqtt_bridge import MqttBridge

log = logging.getLogger("ui.routes")


def register_routes(app: Flask, bridge: MqttBridge) -> None:

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
        # The UI will update `mode_current` when the hub echoes it back
        # retained; don't fabricate local state here.
        return jsonify({"ok": True, "requested": mode})

"""Bridge between paho-mqtt and Flask-SocketIO.

Rule of thumb: MQTT is the source of truth. This module subscribes to the
topics the browser cares about, caches the last value of each retained one,
and re-emits them to Socket.IO clients:

  - on every inbound MQTT message -> socketio.emit(event, payload)
  - on every client `connect`     -> replay the cache so a late-joiner has a
                                     usable UI without waiting for the next
                                     retained republish

Command-direction plumbing (POST /api/mode, /api/fire, etc.) lands in
routes.py; this module exposes a `publish()` passthrough for that path.
"""
import logging
from threading import Lock
from typing import Dict, Set

from flask_socketio import SocketIO

from common.messaging.client import MqttClient
from common.messaging.topics import (
    TOPIC_FIRE_ARMED,
    TOPIC_FIRE_ENABLE,
    TOPIC_FIRE_EVENT,
    TOPIC_HEALTH_ALL,
    TOPIC_HUB_CONFIG,
    TOPIC_HUB_STATE,
    TOPIC_INFER_DETECT,
    TOPIC_MODE_CURRENT,
    TOPIC_MOTION_CTRL_CALIBRATION,
    TOPIC_MOTION_CTRL_ENABLE,
    TOPIC_MOTION_CTRL_STATE,
    TOPIC_RECORDER_EVENT,
    TOPIC_TELEMETRY,
)

log = logging.getLogger("ui.bridge")


def _normalize_detections(payload: dict) -> dict:
    """Coerce detection payloads to {detections: [{class, score, bbox}], ts}.

    Accepts both the canonical multi-detection shape and the single-detection
    shape ({"class","score","bbox"}) that hand-published synthetic test
    payloads use. Anything else passes through unchanged so the UI can choose
    to ignore it.
    """
    if "detections" in payload and isinstance(payload["detections"], list):
        return payload
    if "bbox" in payload and "class" in payload:
        det = {k: payload[k] for k in ("class", "score", "bbox") if k in payload}
        return {"detections": [det], "ts": payload.get("ts")}
    return payload


# topic -> socket.io event name (exact-match topics)
_TOPIC_TO_EVENT: Dict[str, str] = {
    TOPIC_HUB_STATE:           "hub_state",
    TOPIC_HUB_CONFIG:          "hub_config",
    TOPIC_MODE_CURRENT:        "mode_current",
    TOPIC_TELEMETRY:           "telemetry",
    TOPIC_RECORDER_EVENT:      "recorder_event",
    TOPIC_FIRE_ENABLE:         "fire_enable",
    TOPIC_FIRE_ARMED:          "fire_armed",
    TOPIC_FIRE_EVENT:          "fire_event",
    TOPIC_MOTION_CTRL_ENABLE:  "motion_enable",
    TOPIC_MOTION_CTRL_STATE:   "aim_state",
    TOPIC_MOTION_CTRL_CALIBRATION: "motion_calibration",
    TOPIC_INFER_DETECT:        "detection",
}

# wildcard filter -> event name
_WILDCARD_TO_EVENT: Dict[str, str] = {
    TOPIC_HEALTH_ALL: "health",
}

# Topics whose latest value is replayed to fresh socket connections. Must be
# the retained ones; live-only events (telemetry, aim_state) would be stale.
_REPLAY_TOPICS: Set[str] = {
    TOPIC_HUB_STATE,
    TOPIC_HUB_CONFIG,
    TOPIC_MODE_CURRENT,
    TOPIC_FIRE_ENABLE,
    TOPIC_FIRE_ARMED,
    TOPIC_MOTION_CTRL_ENABLE,
    TOPIC_MOTION_CTRL_CALIBRATION,
}


class MqttBridge:
    def __init__(self, socketio: SocketIO):
        self.socketio = socketio
        self.mqtt = MqttClient(service_name="ui-backend")
        self._cache_lock = Lock()
        self._replay_cache: Dict[str, dict] = {}        # event -> payload
        self._latest: Dict[str, dict] = {}              # topic -> payload

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> None:
        self.mqtt.connect()
        for topic in _TOPIC_TO_EVENT:
            self.mqtt.subscribe(topic, self._on_mqtt_message)
        for filt in _WILDCARD_TO_EVENT:
            self.mqtt.subscribe(filt, self._on_mqtt_wildcard)

        self.socketio.on_event("connect", self._on_socket_connect)

    def stop(self) -> None:
        try:
            self.mqtt.disconnect()
        except Exception:
            log.exception("mqtt disconnect failed")

    # ---- command path (Flask routes -> MQTT) ---------------------------

    def publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        """Thin passthrough so routes.py doesn't import MqttClient directly."""
        self.mqtt.publish(topic, payload, retain=retain)

    def get_latest(self, topic: str) -> dict:
        """Last-seen payload for a topic; {} if never seen."""
        with self._cache_lock:
            return dict(self._latest.get(topic, {}))

    # ---- MQTT -> Socket.IO ---------------------------------------------

    def _on_mqtt_message(self, topic: str, payload: dict) -> None:
        event = _TOPIC_TO_EVENT.get(topic)
        if event is None:
            return
        if topic == TOPIC_INFER_DETECT:
            payload = _normalize_detections(payload)
        with self._cache_lock:
            self._latest[topic] = payload
            if topic in _REPLAY_TOPICS:
                self._replay_cache[event] = payload
        self.socketio.emit(event, payload)

    def _on_mqtt_wildcard(self, topic: str, payload: dict) -> None:
        # turret/<host>/health -> forward with the topic so the UI knows which
        # host/service it's about.
        self.socketio.emit("health", {"topic": topic, **payload})

    # ---- Socket.IO <- cache --------------------------------------------

    def _on_socket_connect(self):
        with self._cache_lock:
            snapshot = dict(self._replay_cache)
        for event, payload in snapshot.items():
            self.socketio.emit(event, payload)

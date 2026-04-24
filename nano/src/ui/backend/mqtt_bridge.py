"""Bridge between paho-mqtt and Flask-SocketIO.

Rule of thumb: MQTT is the source of truth. This module subscribes to the
topics the browser cares about, caches the last retained/seen payload of each,
and re-emits them to Socket.IO clients:

  - on every inbound MQTT message -> socketio.emit(event, payload)
  - on every client `connect`     -> replay the cache so a late-joiner has a
                                     usable UI without waiting for the next
                                     retained republish

Command-direction plumbing (POST /api/mode, /api/fire, etc.) lands in
routes.py in later H-steps.
"""
import logging
from threading import Lock
from typing import Dict

from flask_socketio import SocketIO

from common.messaging.client import MqttClient
from common.messaging.topics import (
    TOPIC_HEALTH_ALL,
    TOPIC_HUB_STATE,
    TOPIC_MODE_CURRENT,
    TOPIC_RECORDER_EVENT,
    TOPIC_TELEMETRY,
)

log = logging.getLogger("ui.bridge")


# topic -> socket.io event name
_TOPIC_TO_EVENT: Dict[str, str] = {
    TOPIC_HUB_STATE:       "hub_state",
    TOPIC_MODE_CURRENT:    "mode_current",
    TOPIC_TELEMETRY:       "telemetry",
    TOPIC_RECORDER_EVENT:  "recorder_event",
}

# wildcard filter -> event name (matched via paho's subscriber model)
_WILDCARD_TO_EVENT: Dict[str, str] = {
    TOPIC_HEALTH_ALL: "health",
}


class MqttBridge:
    def __init__(self, socketio: SocketIO):
        self.socketio = socketio
        self.mqtt = MqttClient(service_name="ui-backend")
        self._cache_lock = Lock()
        # Keep only the retained, "always replay on connect" events cached.
        self._replay_cache: Dict[str, dict] = {}

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

    # ---- MQTT -> Socket.IO ---------------------------------------------

    def _on_mqtt_message(self, topic: str, payload: dict) -> None:
        event = _TOPIC_TO_EVENT.get(topic)
        if event is None:
            return
        # Retained hub_state / mode_current are the two we replay on connect;
        # telemetry/recorder_event are live-only.
        if topic in (TOPIC_HUB_STATE, TOPIC_MODE_CURRENT):
            with self._cache_lock:
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

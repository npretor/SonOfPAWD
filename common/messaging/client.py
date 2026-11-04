"""Thin paho-mqtt wrapper with JSON payloads and last-will health.

Designed for paho-mqtt 1.x (Pi OS Bookworm ships 1.6.x via apt). Usage:

    mqtt = MqttClient(service_name="camera-pipeline")
    mqtt.connect()
    mqtt.publish(TOPIC_CAMERA_STATUS, {"rtsp_url": "..."})
    mqtt.subscribe(TOPIC_IR_SET, lambda topic, payload: ...)
    # ... later ...
    mqtt.disconnect()
"""
import json
import logging
import socket
import time
from typing import Callable, Dict, List, Optional

import paho.mqtt.client as mqtt

from common.config.hosts import (
    MQTT_HOST,
    MQTT_PORT,
    MQTT_KEEPALIVE_S,
    MQTT_USERNAME,
    MQTT_PASSWORD_FILE,
)
from common.messaging.topics import topic_health

log = logging.getLogger(__name__)

Handler = Callable[[str, dict], None]


def _read_mqtt_password() -> str:
    try:
        with open(MQTT_PASSWORD_FILE) as f:
            pw = f.read().strip()
    except FileNotFoundError:
        raise RuntimeError(
            f"MQTT password file missing at {MQTT_PASSWORD_FILE}. "
            f"Run nano/scripts/host_prep.sh on the Nano, then scp the file to this host."
        )
    if not pw:
        raise RuntimeError(f"MQTT password file {MQTT_PASSWORD_FILE} is empty.")
    return pw


class MqttClient:
    def __init__(
        self,
        service_name: str,
        host_tag: Optional[str] = None,
        on_connected: Optional[Callable[[], None]] = None,
    ):
        self.service_name = service_name
        self.host_tag = host_tag or self._detect_host_tag()
        self.health_topic = topic_health(self.host_tag)
        self._on_connected_cb = on_connected

        self._client = mqtt.Client(
            client_id=f"{self.host_tag}-{service_name}-{int(time.time())}"
        )
        self._handlers: Dict[str, Handler] = {}
        self._subs: List[str] = []

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    @staticmethod
    def _detect_host_tag() -> str:
        name = socket.gethostname().lower()
        if "pi" in name:
            return "pi"
        return "nano"

    def connect(self, host: str = MQTT_HOST, port: int = MQTT_PORT) -> None:
        self._client.username_pw_set(MQTT_USERNAME, _read_mqtt_password())
        down = json.dumps({
            "ts": int(time.time() * 1000),
            "service": self.service_name,
            "status": "down",
        })
        self._client.will_set(self.health_topic, down, qos=1, retain=False)
        self._client.connect_async(host, port, keepalive=MQTT_KEEPALIVE_S)
        self._client.loop_start()

    def disconnect(self) -> None:
        self.publish(self.health_topic, {
            "service": self.service_name,
            "status": "down",
        })
        self._client.loop_stop()
        self._client.disconnect()

    def is_connected(self) -> bool:
        return self._client.is_connected()

    def publish(self, topic: str, payload: dict, retain: bool = False) -> None:
        if "ts" not in payload:
            payload["ts"] = int(time.time() * 1000)
        self._client.publish(topic, json.dumps(payload), qos=1, retain=retain)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic] = handler
        if topic not in self._subs:
            self._subs.append(topic)
        self._client.subscribe(topic, qos=1)

    def _on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            log.warning("MQTT connect failed rc=%s", rc)
            return
        log.info("MQTT connected as %s/%s", self.host_tag, self.service_name)
        self.publish(self.health_topic, {
            "service": self.service_name,
            "status": "up",
        })
        for topic in self._subs:
            client.subscribe(topic, qos=1)
        if self._on_connected_cb is not None:
            try:
                self._on_connected_cb()
            except Exception:
                log.exception("on_connected callback failed")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            log.warning("MQTT disconnected unexpectedly rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"_raw": repr(msg.payload)}
        for topic_filter, handler in self._handlers.items():
            if mqtt.topic_matches_sub(topic_filter, msg.topic):
                try:
                    handler(msg.topic, payload)
                except Exception:
                    log.exception("Handler for %s raised", topic_filter)

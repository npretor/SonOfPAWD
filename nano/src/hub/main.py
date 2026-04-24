#!/usr/bin/env python3
"""hub: mode orchestrator + telemetry for the Nano.

Step B scope (per core_service_implimentation.md §9):
    - boots into idle; all "dangerous" enable topics published retained=false
    - publishes turret/nano/telemetry every TELEMETRY_PERIOD_S
    - aggregates turret/+/health into turret/hub/state (retained)
    - accepts turret/mode/set and republishes turret/mode/current (retained),
      but only idle is fully wired: motion events are logged, no side effects

Monitor/trailcam/auto-aim/auto-sentry behavior arrives in later steps.
"""
import logging
import signal
import sys
import time

from gi.repository import GLib

from common.config.hosts import TELEMETRY_PERIOD_S
from common.messaging.client import MqttClient
from common.messaging.topics import (
    TOPIC_HEALTH_ALL,
    TOPIC_HUB_STATE,
    TOPIC_FIRE_ENABLE,
    TOPIC_INFER_ENABLE,
    TOPIC_MODE_CURRENT,
    TOPIC_MODE_SET,
    TOPIC_MOTION,
    TOPIC_MOTION_CTRL_ENABLE,
    TOPIC_RECORDER_EVENT,
    TOPIC_TELEMETRY,
)

from nano.src.hub.alerts import alert_user
from nano.src.hub.modes import Mode
from nano.src.hub.state import HubState
from nano.src.hub.telemetry import TelemetryReader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hub")


class Hub:
    def __init__(self):
        self.state = HubState()
        self.telemetry = TelemetryReader()
        self.loop = GLib.MainLoop()
        self.mqtt = MqttClient(service_name="hub", on_connected=self._on_connected)

    # ----- post-connect: publish retained bootstrap state ----------------

    def _on_connected(self) -> None:
        # All "dangerous" surfaces start disabled. Republish on every (re)connect
        # so a broker restart can't leave a stale retained "enabled" out there.
        self.mqtt.publish(TOPIC_MODE_CURRENT, {"mode": self.state.mode.value}, retain=True)
        self.mqtt.publish(TOPIC_INFER_ENABLE, {"enabled": False}, retain=True)
        self.mqtt.publish(TOPIC_MOTION_CTRL_ENABLE, {"enabled": False}, retain=True)
        self.mqtt.publish(TOPIC_FIRE_ENABLE, {"on": False}, retain=True)
        self._publish_hub_state()

    # ----- MQTT subscribers: marshal onto the GLib thread ----------------

    def _on_mode_set(self, _topic, payload):
        GLib.idle_add(self._handle_mode_set, payload)

    def _on_motion(self, _topic, payload):
        GLib.idle_add(self._handle_motion, payload)

    def _on_health(self, topic, payload):
        GLib.idle_add(self._handle_health, topic, payload)

    def _on_recorder_event(self, _topic, payload):
        GLib.idle_add(self._handle_recorder_event, payload)

    # ----- GLib-thread handlers -----------------------------------------

    def _handle_mode_set(self, payload):
        raw = payload.get("mode", "")
        try:
            new_mode = Mode(raw)
        except ValueError:
            log.warning("ignoring invalid mode: %r", raw)
            return False
        if new_mode == self.state.mode:
            return False
        old = self.state.mode
        self.state.mode = new_mode
        # Step B: only idle is fully wired. Non-idle modes still accept the
        # transition and republish state; their motion handling lands later.
        log.info("mode: %s -> %s", old.value, new_mode.value)
        self.mqtt.publish(TOPIC_MODE_CURRENT, {"mode": new_mode.value}, retain=True)
        self._publish_hub_state()
        return False

    def _handle_motion(self, payload):
        active = bool(payload.get("active", False))
        if active:
            self.state.last_motion_ts = time.monotonic()
        if self.state.mode == Mode.IDLE:
            log.info("motion active=%s (idle; no side effects)", active)
        else:
            # Will expand in Step C and beyond.
            log.info("motion active=%s in mode=%s (not yet wired)",
                     active, self.state.mode.value)
        return False

    def _handle_health(self, topic, payload):
        parts = topic.split("/")
        host = parts[1] if len(parts) >= 3 else "?"
        svc = payload.get("service", "?")
        status = payload.get("status", "?")
        key = f"{host}/{svc}"
        if self.state.peers.get(key) != status:
            log.info("health: %s = %s", key, status)
        self.state.peers[key] = status
        self._publish_hub_state()
        return False

    def _handle_recorder_event(self, payload):
        event = payload.get("event", "?")
        if event == "clip_started":
            self.state.recorder_armed = True
            self.state.ir_recording = True
        elif event == "clip_closed":
            self.state.recorder_armed = False
            self.state.ir_recording = False
        self._publish_hub_state()
        return False

    # ----- periodic publishes -------------------------------------------

    def _tick_telemetry(self):
        self.mqtt.publish(TOPIC_TELEMETRY, self.telemetry.sample())
        return True  # keep firing

    def _publish_hub_state(self):
        self.mqtt.publish(TOPIC_HUB_STATE, {
            "mode": self.state.mode.value,
            "services": dict(self.state.peers),
            "armed": self.state.recorder_armed,
            "ir_on": self.state.ir_on(),
        }, retain=True)

    # ----- lifecycle ----------------------------------------------------

    def run(self) -> int:
        self.mqtt.connect()
        self.mqtt.subscribe(TOPIC_MODE_SET, self._on_mode_set)
        self.mqtt.subscribe(TOPIC_MOTION, self._on_motion)
        self.mqtt.subscribe(TOPIC_HEALTH_ALL, self._on_health)
        self.mqtt.subscribe(TOPIC_RECORDER_EVENT, self._on_recorder_event)

        GLib.timeout_add_seconds(TELEMETRY_PERIOD_S, self._tick_telemetry)
        alert_user("info", "hub starting in idle mode", mqtt=self.mqtt)

        def shutdown(signum, _frame):
            log.info("signal %d received, shutting down", signum)
            try:
                self.mqtt.disconnect()
            except Exception:
                log.exception("mqtt disconnect failed")
            self.loop.quit()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        self.loop.run()
        return 0


def main() -> int:
    return Hub().run()


if __name__ == "__main__":
    sys.exit(main())

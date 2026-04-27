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
    TOPIC_HUB_CONFIG,
    TOPIC_HUB_STATE,
    TOPIC_FIRE_ENABLE,
    TOPIC_INFER_DETECT,
    TOPIC_INFER_ENABLE,
    TOPIC_MODE_CURRENT,
    TOPIC_MODE_SET,
    TOPIC_MOTION,
    TOPIC_MOTION_CTRL_AIM,
    TOPIC_MOTION_CTRL_ENABLE,
    TOPIC_RECORDER_EVENT,
    TOPIC_TELEMETRY,
)

# Valid target classes for hub/config validation. Mirrors the UI whitelist
# (nano/src/ui/backend/settings.py); defined here so the hub can fall back
# without importing UI code.
_VALID_TARGET_CLASSES = {"cat", "raccoon", "rat", "deer"}

# Modes in which the hub drives the actuator + inference pipeline.
_AUTO_MODES = {"auto-aim", "auto-sentry"}
DETECTION_SCORE_MIN = 0.5

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
        # (Removed: aim->state stub echo. Owned by pi/src/motion_ctrl now.)
        # Hub-config bootstrap: only publish defaults if no retained value
        # already exists on the broker. Set when the first hub_config arrives.
        self._seen_hub_config = False

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

    def _on_hub_config(self, _topic, payload):
        GLib.idle_add(self._handle_hub_config, payload)

    def _on_detection(self, _topic, payload):
        GLib.idle_add(self._handle_detection, payload)

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
        log.info("mode: %s -> %s", old.value, new_mode.value)
        self._apply_mode_side_effects(old, new_mode)
        self.mqtt.publish(TOPIC_MODE_CURRENT, {"mode": new_mode.value}, retain=True)
        self._publish_hub_state()
        return False

    def _apply_mode_side_effects(self, old: Mode, new: Mode) -> None:
        """Toggle motion_ctrl + inference enable when entering/leaving auto modes."""
        was_auto = old.value in _AUTO_MODES
        is_auto = new.value in _AUTO_MODES
        if was_auto == is_auto:
            return
        self.mqtt.publish(TOPIC_MOTION_CTRL_ENABLE, {"enabled": is_auto}, retain=True)
        self.mqtt.publish(TOPIC_INFER_ENABLE, {"enabled": is_auto}, retain=True)
        log.info("auto-mode side effects: motion_ctrl/enable=%s inference/enable=%s",
                 is_auto, is_auto)

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

    def _handle_hub_config(self, payload):
        # The UI publishes the user's request retained; the hub validates +
        # clamps and stores. We don't republish on every receipt — that would
        # ping-pong with our own retained message. We only republish when our
        # normalized value differs from what arrived (i.e. the UI sent us
        # garbage and we want to correct the retained record).
        self._seen_hub_config = True
        before = self._effective_config()
        changed = False
        if "motion_timeout_s" in payload:
            try:
                v = max(1, min(600, int(payload["motion_timeout_s"])))
                if v != self.state.motion_timeout_s:
                    self.state.motion_timeout_s = v
                    changed = True
            except (TypeError, ValueError):
                pass
        if "trailing_s" in payload:
            try:
                v = max(1, min(300, int(payload["trailing_s"])))
                if v != self.state.trailing_s:
                    self.state.trailing_s = v
                    changed = True
            except (TypeError, ValueError):
                pass
        if "target_class" in payload:
            tc = payload["target_class"]
            if tc in _VALID_TARGET_CLASSES and tc != self.state.target_class:
                self.state.target_class = tc
                changed = True

        effective = self._effective_config()
        # Republish only if (a) we actually changed something AND (b) the
        # incoming payload didn't already match the effective view. The second
        # check breaks the self-publish echo loop.
        if changed and any(payload.get(k) != effective[k] for k in effective):
            log.info("hub_config: %s -> %s", before, effective)
            self.mqtt.publish(TOPIC_HUB_CONFIG, effective, retain=True)
        elif changed:
            log.info("hub_config: %s -> %s (no echo)", before, effective)
        return False

    def _handle_detection(self, payload):
        if self.state.mode.value not in _AUTO_MODES:
            return False
        # Canonical contract: {"detections": [...], "ts": ...}. Single-shape
        # ({"class","score","bbox"}) is also accepted so synthetic test
        # publishes still work.
        if isinstance(payload.get("detections"), list):
            dets = payload["detections"]
        elif "bbox" in payload and "class" in payload:
            dets = [payload]
        else:
            return False

        best = None  # (score, cx, cy)
        for d in dets:
            if not isinstance(d, dict):
                continue
            if d.get("class") != self.state.target_class:
                continue
            try:
                score = float(d.get("score", 0.0))
            except (TypeError, ValueError):
                continue
            if score < DETECTION_SCORE_MIN:
                continue
            bbox = d.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(v) for v in bbox)
            except (TypeError, ValueError):
                continue
            cx = (x0 + x1) / 2.0
            cy = (y0 + y1) / 2.0
            if best is None or score > best[0]:
                best = (score, cx, cy)

        if best is None:
            return False
        _, cx, cy = best
        # +pan = right, +tilt = up; image y grows downward.
        pan = max(-1.0, min(1.0, 2.0 * cx - 1.0))
        tilt = max(-1.0, min(1.0, 1.0 - 2.0 * cy))
        self.mqtt.publish(TOPIC_MOTION_CTRL_AIM, {"pan": pan, "tilt": tilt})
        return False

    def _bootstrap_hub_config(self):
        if not self._seen_hub_config:
            log.info("no retained hub_config seen; publishing defaults: %s",
                     self._effective_config())
            self.mqtt.publish(TOPIC_HUB_CONFIG, self._effective_config(), retain=True)
        return False  # one-shot

    def _effective_config(self) -> dict:
        return {
            "motion_timeout_s": self.state.motion_timeout_s,
            "trailing_s": self.state.trailing_s,
            "target_class": self.state.target_class,
        }

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
        self.mqtt.subscribe(TOPIC_HUB_CONFIG, self._on_hub_config)
        self.mqtt.subscribe(TOPIC_INFER_DETECT, self._on_detection)

        GLib.timeout_add_seconds(TELEMETRY_PERIOD_S, self._tick_telemetry)
        # Give the broker ~500ms to deliver a retained TOPIC_HUB_CONFIG (if any)
        # before falling back to publishing defaults. Avoids clobbering user
        # settings on every hub restart.
        GLib.timeout_add(500, self._bootstrap_hub_config)
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

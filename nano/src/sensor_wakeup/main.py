#!/usr/bin/env python3
"""sensor-wakeup: PIR GPIO -> MQTT turret/motion.

Polls the PIR level at POLL_HZ:
  level goes high                      -> publish active=true (once)
  level low for PIR_INACTIVE_AFTER_S   -> publish active=false (once)

Level polling (rather than rising-edge callbacks) keeps a PIR that holds its
output high during continuous motion reported as active. The edge-only
version declared it inactive after PIR_INACTIVE_AFTER_S even while the
sensor was still high, and never re-announced it.
"""
import logging
import signal
import threading
import time

from common.config.hosts import PIR_PIN, PIR_INACTIVE_AFTER_S
from common.messaging.client import MqttClient
from common.messaging.topics import TOPIC_MOTION
from nano.src.hw.gpio import DigitalInput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sensor-wakeup")

POLL_HZ = 20


class MotionTracker:
    """Pure state machine; no I/O, so it's testable without hardware."""

    def __init__(self, inactive_after_s: float):
        self.inactive_after_s = inactive_after_s
        self.active = False
        self._last_high = 0.0

    def update(self, level: bool, now: float):
        """Return True/False when a transition should be published, else None."""
        if level:
            self._last_high = now
            if not self.active:
                self.active = True
                return True
        elif self.active and (now - self._last_high) > self.inactive_after_s:
            self.active = False
            return False
        return None


def main() -> int:
    mqtt = MqttClient(service_name="sensor-wakeup")
    mqtt.connect()

    pir = DigitalInput(PIR_PIN)
    tracker = MotionTracker(PIR_INACTIVE_AFTER_S)

    def publish(active: bool) -> None:
        mqtt.publish(TOPIC_MOTION, {"sensor": "pir", "active": active})
        log.info("motion active=%s", active)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    log.info("PIR on BCM pin %d ready (poll %d Hz)", PIR_PIN, POLL_HZ)
    period = 1.0 / POLL_HZ
    try:
        while not stop.is_set():
            change = tracker.update(pir.value, time.monotonic())
            if change is not None:
                publish(change)
            stop.wait(period)
    finally:
        if tracker.active:
            publish(False)
        mqtt.disconnect()
        pir.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

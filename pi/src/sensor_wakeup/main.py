#!/usr/bin/env python3
"""sensor-wakeup: PIR GPIO -> MQTT turret/motion.

Rising edge -> publish active=true (once).
No motion for PIR_INACTIVE_AFTER_S -> publish active=false (once).
"""
import logging
import signal
import threading
import time

from gpiozero import MotionSensor

from common.config.hosts import PIR_PIN, PIR_INACTIVE_AFTER_S
from common.messaging.client import MqttClient
from common.messaging.topics import TOPIC_MOTION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sensor-wakeup")


def main() -> int:
    mqtt = MqttClient(service_name="sensor-wakeup")
    mqtt.connect()

    pir = MotionSensor(PIR_PIN)

    state_lock = threading.Lock()
    last_active_at = 0.0
    currently_active = False

    def publish(active: bool) -> None:
        mqtt.publish(TOPIC_MOTION, {"sensor": "pir", "active": active})
        log.info("motion active=%s", active)

    def on_motion() -> None:
        nonlocal last_active_at, currently_active
        with state_lock:
            last_active_at = time.monotonic()
            was_active = currently_active
            currently_active = True
        if not was_active:
            publish(True)

    pir.when_motion = on_motion

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    log.info("PIR on BCM pin %d ready", PIR_PIN)

    while not stop.is_set():
        with state_lock:
            active = currently_active
            last = last_active_at
        if active and (time.monotonic() - last) > PIR_INACTIVE_AFTER_S:
            with state_lock:
                currently_active = False
            publish(False)
        stop.wait(0.1)

    with state_lock:
        if currently_active:
            publish(False)
    mqtt.disconnect()
    pir.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

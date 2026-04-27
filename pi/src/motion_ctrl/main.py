#!/usr/bin/env python3
"""motion-ctrl: MQTT aim/enable -> Arduino servo controller.

Phase 1 of motor_control_plan.md. Subscribes:
    turret/motion_ctrl/enable    {"enabled": bool}     (retained)
    turret/motion_ctrl/aim       {"pan": -1..1, "tilt": -1..1}

Publishes:
    turret/motion_ctrl/state     at <= state_publish_hz
    turret/pi/health             via MqttClient (last-will + degraded on serial loss)
"""
import logging
import signal
import threading
import time

from gpiozero import DigitalOutputDevice

from common.config.hosts import (
    SERVO_POWER_PIN,
    SERVO_SERIAL_BAUD,
    SERVO_SERIAL_DEV,
)
from common.messaging.client import MqttClient
from common.messaging.topics import (
    TOPIC_MOTION_CTRL_AIM,
    TOPIC_MOTION_CTRL_ENABLE,
    TOPIC_MOTION_CTRL_STATE,
)

from pi.src.motion_ctrl.calibration import DEFAULT
from pi.src.motion_ctrl.servo_link import ServoLink
from pi.src.motion_ctrl.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("motion-ctrl")


def main() -> int:
    cal = DEFAULT
    link = ServoLink(SERVO_SERIAL_DEV, SERVO_SERIAL_BAUD)
    # initial_value=False is the safety contract — servos must never come up
    # energized into an unknown commanded position.
    psu = DigitalOutputDevice(SERVO_POWER_PIN, active_high=True, initial_value=False)
    worker = Worker(link=link, psu=psu, cal=cal)

    mqtt = MqttClient(service_name="motion-ctrl")

    def publish_state(payload: dict) -> None:
        mqtt.publish(TOPIC_MOTION_CTRL_STATE, payload)

    def publish_health(status: str, detail: str) -> None:
        mqtt.publish(mqtt.health_topic, {
            "service": "motion-ctrl",
            "status": status,
            "detail": detail,
        })

    worker.publish_state = publish_state
    worker.publish_health = publish_health

    def on_enable(_topic: str, payload: dict) -> None:
        worker.set_enabled(bool(payload.get("enabled", False)))

    def on_aim(_topic: str, payload: dict) -> None:
        try:
            pan = float(payload.get("pan", 0.0))
            tilt = float(payload.get("tilt", 0.0))
        except (TypeError, ValueError):
            return
        worker.set_aim(pan, tilt)

    mqtt.connect()
    mqtt.subscribe(TOPIC_MOTION_CTRL_ENABLE, on_enable)
    mqtt.subscribe(TOPIC_MOTION_CTRL_AIM, on_aim)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    log.info(
        "motion-ctrl ready (PSU=BCM%d, serial=%s @ %d, tick=%dHz)",
        SERVO_POWER_PIN, SERVO_SERIAL_DEV, SERVO_SERIAL_BAUD, cal.tick_hz,
    )

    period = 1.0 / cal.tick_hz
    next_tick = time.monotonic()
    try:
        while not stop.is_set():
            worker.tick()
            next_tick += period
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                stop.wait(sleep_for)
            else:
                # Fell behind — reset baseline so we don't spin chasing the past.
                next_tick = time.monotonic()
    finally:
        worker.shutdown()
        try:
            mqtt.disconnect()
        except Exception:
            log.exception("mqtt disconnect failed")
        psu.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

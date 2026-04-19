#!/usr/bin/env python3
"""ir-led: MQTT turret/ir/set -> GPIO high/low.

LED is forced off on service shutdown regardless of cause.
"""
import logging
import signal
import threading

from gpiozero import DigitalOutputDevice

from common.config.hosts import IR_LED_PIN
from common.messaging.client import MqttClient
from common.messaging.topics import TOPIC_IR_SET

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ir-led")


def main() -> int:
    led = DigitalOutputDevice(IR_LED_PIN, active_high=True, initial_value=False)
    mqtt = MqttClient(service_name="ir-led")

    def on_ir_set(_topic: str, payload: dict) -> None:
        on = bool(payload.get("on", False))
        if on:
            led.on()
        else:
            led.off()
        log.info("IR LED -> %s", "on" if on else "off")

    mqtt.connect()
    mqtt.subscribe(TOPIC_IR_SET, on_ir_set)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    log.info("IR LED on BCM pin %d ready", IR_LED_PIN)
    try:
        stop.wait()
    finally:
        led.off()
        mqtt.disconnect()
        led.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

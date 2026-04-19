#!/usr/bin/env python3
"""Subscribe to everything under turret/ and print. Ctrl-C to stop.

    python3 -m common.messaging.examples.sub_test
"""
import logging
import signal
import threading

from common.messaging.client import MqttClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    mqtt = MqttClient(service_name="sub-test")
    stop = threading.Event()

    def on_msg(topic, payload):
        print(f"[{topic}] {payload}")

    mqtt.connect()
    mqtt.subscribe("turret/#", on_msg)

    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    mqtt.disconnect()


if __name__ == "__main__":
    main()

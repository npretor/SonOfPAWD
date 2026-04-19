#!/usr/bin/env python3
"""Publish a handful of test messages. Run on Pi or Nano.

    python3 -m common.messaging.examples.pub_test
"""
import logging
import time

from common.messaging.client import MqttClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main():
    mqtt = MqttClient(service_name="pub-test")
    mqtt.connect()
    time.sleep(0.5)  # let connect settle
    for i in range(5):
        mqtt.publish("turret/test/ping", {"i": i, "from": mqtt.host_tag})
        print(f"pub turret/test/ping i={i}")
        time.sleep(1)
    mqtt.disconnect()


if __name__ == "__main__":
    main()

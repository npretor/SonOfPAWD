#!/usr/bin/env python3
"""fire-control: MQTT fire gates/requests -> enable + trigger GPIO.

Subscribes:
    turret/fire/armed     {"on": bool}  retained, UI manual arm
    turret/fire/enable    {"on": bool}  retained, hub mode-driven auto-fire
    turret/fire/request   {"source": "ui"|"auto-sentry", "pulse_ms"?: int}
    turret/nano/health    the hub's up/down (last-will) -> immediate
    turret/nano/telemetry the hub's ~5 s heartbeat; stale > HUB_STALE_S = hub dead
                          (health isn't retained, so this is also how we learn
                          the hub is alive when we start after it)
Publishes:
    turret/fire/event     fired / rejected / enable
    turret/fire/armed     {"on": false} retained, when the hub dies (so the UI
                          shows the disarm that actually happened)

See controller.py for the authorization rules. Pins are forced low by the
unit's ExecStopPost even if this process is SIGKILLed (nano/src/hw/force_low.py).
"""
import logging
import queue
import signal
import threading
import time

from common.config.hosts import (
    TELEMETRY_PERIOD_S,
    FIRE_ENABLE_PIN,
    FIRE_TRIGGER_PIN,
    FIRE_PULSE_MS,
    FIRE_MAX_PULSE_MS,
    FIRE_MIN_INTERVAL_S,
)
from common.messaging.client import MqttClient
from common.messaging.topics import (
    TOPIC_FIRE_ARMED,
    TOPIC_FIRE_ENABLE,
    TOPIC_FIRE_EVENT,
    TOPIC_FIRE_REQUEST,
    TOPIC_TELEMETRY,
    topic_health,
)
from nano.src.fire_control.controller import FireController
from nano.src.hw.gpio import DigitalOutput

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fire-control")

LINK_CHECK_S = 0.2
# Three missed telemetry periods = hub hung or gone.
HUB_STALE_S = 3 * TELEMETRY_PERIOD_S + 1


def main() -> int:
    # Trigger first so it's low before enable can ever be touched.
    trigger = DigitalOutput(FIRE_TRIGGER_PIN, initial=False)
    enable = DigitalOutput(FIRE_ENABLE_PIN, initial=False)

    mqtt = MqttClient(service_name="fire-control")

    def publish_event(ev: dict) -> None:
        try:
            mqtt.publish(TOPIC_FIRE_EVENT, ev)
        except Exception:
            log.exception("fire event publish failed")

    ctl = FireController(
        enable_out=enable,
        trigger_out=trigger,
        pulse_ms=FIRE_PULSE_MS,
        max_pulse_ms=FIRE_MAX_PULSE_MS,
        min_interval_s=FIRE_MIN_INTERVAL_S,
        publish_event=publish_event,
    )

    # Pulses run on their own thread so a 250 ms pulse never blocks the MQTT
    # loop, which must stay free to deliver a disarm mid-pulse.
    requests: "queue.Queue[dict]" = queue.Queue(maxsize=1)

    def on_request(_t, payload: dict) -> None:
        try:
            requests.put_nowait(payload)
        except queue.Full:
            publish_event({"event": "rejected", "reason": "busy",
                           "source": str(payload.get("source", "unknown"))})

    hub_seen = {"t": 0.0}   # monotonic time of last sign of life; 0 = dead

    def on_health(_t, payload: dict) -> None:
        if payload.get("service") != "hub":
            return
        hub_seen["t"] = time.monotonic() if payload.get("status") == "up" else 0.0
        update_hub()

    def on_telemetry(_t, _payload: dict) -> None:
        hub_seen["t"] = time.monotonic()
        update_hub()

    def update_hub() -> None:
        alive = hub_seen["t"] > 0 and (time.monotonic() - hub_seen["t"]) < HUB_STALE_S
        if ctl.set_hub_alive(alive):
            mqtt.publish(TOPIC_FIRE_ARMED, {"on": False}, retain=True)
            log.warning("hub down/stale: disarmed and cleared retained fire/armed")

    mqtt.subscribe(TOPIC_FIRE_ARMED, lambda _t, p: ctl.set_armed(bool(p.get("on", False))))
    mqtt.subscribe(TOPIC_FIRE_ENABLE, lambda _t, p: ctl.set_auto(bool(p.get("on", False))))
    mqtt.subscribe(TOPIC_FIRE_REQUEST, on_request)
    mqtt.subscribe(topic_health("nano"), on_health)
    mqtt.subscribe(TOPIC_TELEMETRY, on_telemetry)
    mqtt.connect()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    def worker() -> None:
        while not stop.is_set():
            try:
                payload = requests.get(timeout=0.2)
            except queue.Empty:
                continue
            ctl.fire(payload)

    t = threading.Thread(target=worker, name="fire-worker", daemon=True)
    t.start()

    log.info("fire-control ready (enable=BCM%d trigger=BCM%d pulse=%dms max=%dms min_interval=%.2fs)",
             FIRE_ENABLE_PIN, FIRE_TRIGGER_PIN, FIRE_PULSE_MS, FIRE_MAX_PULSE_MS,
             FIRE_MIN_INTERVAL_S)
    log.info("waiting for the hub's health message before enabling (fail closed)")
    try:
        while not stop.is_set():
            # MQTT down = can't hear a disarm -> drop enable.
            ctl.set_link_up(mqtt.is_connected())
            update_hub()  # catches a hub that went silent without a last-will
            stop.wait(LINK_CHECK_S)
    finally:
        ctl.shutdown()
        trigger.close()
        enable.close()
        t.join(timeout=1.0)
        try:
            mqtt.disconnect()
        except Exception:
            log.exception("mqtt disconnect failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

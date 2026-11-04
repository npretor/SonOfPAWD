#!/usr/bin/env python3
"""Force actuator pins LOW, then release them. Used as systemd ExecStopPost=.

Why: on L4T 32 Jetson.GPIO drives pins through sysfs, and a pin stays
exported *and keeps driving its last value* when the owning process dies
without cleanup (SIGKILL, OOM kill, segfault). Measured 2026-11-04: BCM24
stayed HIGH after `kill -9`. ExecStopPost runs after every exit, clean or
not, so this closes that hole.

After release the pin floats, and the pull-down on each optoisolator input
holds it off.

Usage: python3 -m nano.src.hw.force_low 24 [27 ...]    (BCM numbers)
Exits 0 even if a pin fails, so a stop is never reported as failed because
of cleanup; failures are logged to the journal.
"""
import logging
import sys

import Jetson.GPIO as GPIO

logging.basicConfig(level=logging.INFO, format="%(levelname)s force-low: %(message)s")
log = logging.getLogger("force-low")


def main(argv) -> int:
    pins = [int(a) for a in argv]
    if not pins:
        log.error("no pins given")
        return 0
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pin in pins:
        try:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            GPIO.output(pin, GPIO.LOW)
            GPIO.cleanup(pin)
            log.info("BCM%d forced low and released", pin)
        except Exception as e:
            log.error("BCM%d: %s", pin, e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

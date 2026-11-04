"""Thin Jetson.GPIO wrapper with the slice of the gpiozero API our services use.

Pin numbers are BCM (as on the Pi). Jetson.GPIO's BCM mode maps them to the
same physical 40-pin header positions, so wiring moves 1:1 from the Pi.

Safety contract for outputs: they start LOW, and close() drives LOW *before*
releasing the pin. Every actuator pin also has a pull-down on its
optoisolator input, so a released (floating) Nano pin reads as off.

Jetson.GPIO cannot set internal pull-up/down resistors on the Nano, so inputs
rely on the external circuit (the PIR actively drives its output).
"""
import logging
import threading

import Jetson.GPIO as GPIO

log = logging.getLogger("hw.gpio")

_mode_lock = threading.Lock()
_mode_set = False


def _ensure_mode() -> None:
    global _mode_set
    with _mode_lock:
        if not _mode_set:
            # Another process (or a crashed earlier run) may still have the
            # channel exported; that's expected, not worth a warning.
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            _mode_set = True


class DigitalOutput:
    """Active-high output. Mirrors gpiozero.DigitalOutputDevice's on/off/close."""

    def __init__(self, bcm: int, initial: bool = False):
        _ensure_mode()
        self.pin = bcm
        self._lock = threading.Lock()
        self._closed = False
        GPIO.setup(bcm, GPIO.OUT, initial=GPIO.HIGH if initial else GPIO.LOW)
        self._value = bool(initial)

    def on(self) -> None:
        self._write(True)

    def off(self) -> None:
        self._write(False)

    @property
    def value(self) -> bool:
        return self._value

    def _write(self, v: bool) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"BCM{self.pin} already closed")
            GPIO.output(self.pin, GPIO.HIGH if v else GPIO.LOW)
            self._value = v

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                GPIO.output(self.pin, GPIO.LOW)
            except Exception:
                log.exception("BCM%d: drive-low on close failed", self.pin)
            self._value = False
            try:
                GPIO.cleanup(self.pin)
            except Exception:
                log.exception("BCM%d: cleanup failed", self.pin)
            self._closed = True


class DigitalInput:
    """Plain level input (no software pull; see module docstring)."""

    def __init__(self, bcm: int):
        _ensure_mode()
        self.pin = bcm
        GPIO.setup(bcm, GPIO.IN)
        self._closed = False

    @property
    def value(self) -> bool:
        return GPIO.input(self.pin) == GPIO.HIGH

    def close(self) -> None:
        if not self._closed:
            try:
                GPIO.cleanup(self.pin)
            except Exception:
                log.exception("BCM%d: cleanup failed", self.pin)
            self._closed = True

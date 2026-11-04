"""pyserial wrapper for the Arduino servo controller.

Wire protocol (motor_control_brainstorm.md):
    <pan_us, tilt_us>\n   e.g. <1500, 1500>

Connection is best-effort: write_pan_tilt() returns True on success, False
on failure (and marks the link as down so the next call attempts a reopen).
The worker uses the return value to drive its health publish.
"""
import logging
import threading
from typing import Optional

import serial

log = logging.getLogger("motion-ctrl.serial")


class ServoLink:
    def __init__(self, dev: str, baud: int):
        self.dev = dev
        self.baud = baud
        self._lock = threading.Lock()
        self._ser: Optional[serial.Serial] = None
        self._last_open_error: Optional[str] = None

    def open(self) -> bool:
        with self._lock:
            return self._open_locked()

    def _open_locked(self) -> bool:
        if self._ser is not None:
            return True
        try:
            self._ser = serial.Serial(
                self.dev, self.baud, timeout=0.2, write_timeout=0.2,
            )
            log.info("serial open: %s @ %d", self.dev, self.baud)
            self._last_open_error = None
            return True
        except (serial.SerialException, OSError) as e:
            self._ser = None
            msg = f"{type(e).__name__}: {e}"
            if msg != self._last_open_error:
                log.warning("serial open failed: %s", msg)
                self._last_open_error = msg
            return False

    def is_open(self) -> bool:
        return self._ser is not None

    def write_pan_tilt(self, pan_us: int, tilt_us: int) -> bool:
        frame = f"<{pan_us}, {tilt_us}>\n".encode("ascii")
        with self._lock:
            if self._ser is None and not self._open_locked():
                return False
            try:
                self._ser.write(frame)
                return True
            except (serial.SerialException, OSError) as e:
                log.warning("serial write failed (%s); will reopen", e)
                self._close_locked()
                return False

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

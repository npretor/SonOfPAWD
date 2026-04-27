"""Motion-ctrl worker: state machine + tick loop.

State machine (motor_control_plan.md §6.2):

    DOWN ── enable=true ──► CENTERING ── settle ──► READY
      ▲                                              │
      └──────── enable=false ─────────────────────────┘

DOWN: PSU GPIO low. Aim drops on the floor. Serial may or may not be open.
CENTERING: write <center, center> to serial first, *then* energize PSU.
    This avoids a startup snap if the controller had been left at a
    non-center commanded value.
READY: latest aim coalesced and slewed at tick_hz, written to serial.

Inbound aim is just stored — the tick loop reads it. Likewise for enable
(stored, applied at next tick). All cross-thread state is behind one lock.
"""
import logging
import threading
import time
from typing import Optional, Tuple

from pi.src.motion_ctrl.calibration import (
    Calibration,
    DEFAULT,
    slew,
    us_to_axis,
)
from pi.src.motion_ctrl.servo_link import ServoLink

log = logging.getLogger("motion-ctrl.worker")

_CENTERING_SETTLE_S = 0.2


class Worker:
    def __init__(
        self,
        link: ServoLink,
        psu,  # gpiozero.DigitalOutputDevice
        cal: Calibration = DEFAULT,
    ):
        self.link = link
        self.psu = psu
        self.cal = cal
        self._lock = threading.Lock()

        self._state = "DOWN"
        self._desired_enabled = False
        self._latest_aim: Tuple[float, float] = (0.0, 0.0)
        self._current_us: Tuple[int, int] = (cal.pan.center_us, cal.tilt.center_us)
        self._centering_started_at: float = 0.0
        self._aim_drop_logged = False

        # Health/state publish hooks installed by main.py (avoids importing
        # MQTT here; keeps worker.py unit-testable).
        self.publish_state = lambda payload: None  # type: ignore
        self.publish_health = lambda status, detail: None  # type: ignore

        self._last_state_pub_at: float = 0.0
        self._last_serial_ok: Optional[bool] = None

    # ----- inbound -------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._desired_enabled == enabled:
                return
            self._desired_enabled = enabled
            log.info("enable -> %s", enabled)

    def set_calibration(self, cal: Calibration) -> None:
        """Hot-swap calibration. Slew limiter still applies, so any sudden
        change in the us mapping is bounded to max_slew_us_per_tick per axis.
        """
        with self._lock:
            self.cal = cal
        log.info(
            "calibration updated: pan=%s tilt=%s slew=%dus/tick",
            cal.pan, cal.tilt, cal.max_slew_us_per_tick,
        )

    def set_aim(self, pan: float, tilt: float) -> None:
        # Clamp defensively; UI and hub already do this but the worker is
        # the last line.
        pan = max(-1.0, min(1.0, float(pan)))
        tilt = max(-1.0, min(1.0, float(tilt)))
        with self._lock:
            self._latest_aim = (pan, tilt)

    # ----- tick ----------------------------------------------------------

    def tick(self) -> None:
        """Run one cycle. Caller drives at cal.tick_hz."""
        with self._lock:
            state = self._state
            desired = self._desired_enabled
            aim = self._latest_aim
            current_us = self._current_us
            centering_started_at = self._centering_started_at

        # Transitions out of READY/CENTERING when disabled.
        if not desired and state != "DOWN":
            self._enter_down()
            return

        if state == "DOWN":
            if desired:
                self._enter_centering()
            return

        if state == "CENTERING":
            if time.monotonic() - centering_started_at >= _CENTERING_SETTLE_S:
                self._enter_ready()
            return

        # READY: slew toward target, write serial, publish state.
        target_us = (
            self.cal.pan.to_us(aim[0]),
            self.cal.tilt.to_us(aim[1]),
        )
        new_us = (
            slew(current_us[0], target_us[0], self.cal.max_slew_us_per_tick),
            slew(current_us[1], target_us[1], self.cal.max_slew_us_per_tick),
        )

        ok = self.link.write_pan_tilt(new_us[0], new_us[1])
        self._note_serial(ok)
        if ok:
            with self._lock:
                self._current_us = new_us
            self._maybe_publish_state(new_us, enabled=True)

    # ----- transitions ---------------------------------------------------

    def _enter_centering(self) -> None:
        log.info("DOWN -> CENTERING")
        # Send center *before* energizing PSU so the controller isn't asked
        # to snap from some stale commanded value.
        center_us = (self.cal.pan.center_us, self.cal.tilt.center_us)
        ok = self.link.write_pan_tilt(*center_us)
        self._note_serial(ok)
        if not ok:
            # Hold off energizing until the link comes back; stay DOWN.
            return
        try:
            self.psu.on()
        except Exception:
            log.exception("PSU enable failed")
            return
        with self._lock:
            self._state = "CENTERING"
            self._current_us = center_us
            self._centering_started_at = time.monotonic()
        self._maybe_publish_state(center_us, enabled=True, force=True)

    def _enter_ready(self) -> None:
        log.info("CENTERING -> READY")
        with self._lock:
            self._state = "READY"
            self._aim_drop_logged = False

    def _enter_down(self) -> None:
        log.info("%s -> DOWN", self._state)
        try:
            self.psu.off()
        except Exception:
            log.exception("PSU disable failed")
        with self._lock:
            self._state = "DOWN"
            current = self._current_us
        # One last state publish so the UI sees enabled=false promptly.
        self._maybe_publish_state(current, enabled=False, force=True)

    # ----- health + state --------------------------------------------------

    def _note_serial(self, ok: bool) -> None:
        if ok == self._last_serial_ok:
            return
        self._last_serial_ok = ok
        if ok:
            self.publish_health("up", "")
        else:
            self.publish_health("degraded", f"serial write failed ({self.link.dev})")

    def _maybe_publish_state(
        self,
        us: Tuple[int, int],
        enabled: bool,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        period = 1.0 / max(1, self.cal.state_publish_hz)
        if not force and (now - self._last_state_pub_at) < period:
            return
        self._last_state_pub_at = now
        self.publish_state({
            "pan": us_to_axis(us[0], self.cal.pan),
            "tilt": us_to_axis(us[1], self.cal.tilt),
            "pan_us": us[0],
            "tilt_us": us[1],
            "enabled": enabled,
        })

    # ----- shutdown ------------------------------------------------------

    def shutdown(self) -> None:
        log.info("shutdown: cutting PSU and centering serial")
        try:
            self.psu.off()
        except Exception:
            log.exception("PSU off during shutdown failed")
        # Best-effort park-at-center; failure is fine, power is already gone.
        self.link.write_pan_tilt(self.cal.pan.center_us, self.cal.tilt.center_us)
        self.link.close()

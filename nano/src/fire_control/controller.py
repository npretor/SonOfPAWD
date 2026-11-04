"""Fire-control logic, hardware- and MQTT-free so it can be tested with fakes.

Pins (both active high, both into optoisolators with pull-downs):
    enable   high only while firing is authorized
    trigger  pulsed high for pulse_ms per accepted request

Authorization (core_service_implimentation.md §3.2, updated for fire/armed):
    enable pin = (fire/armed OR fire/enable) AND hub alive AND MQTT connected
    'ui' requests     need armed OR auto-enable  (mirrors ui/backend/routes.py)
    'auto-sentry'     needs armed AND auto-enable (mirrors hub._maybe_fire)
    unknown source    treated like auto-sentry (the strictest rule)
Every request also needs the enable pin high *at that instant*.

Order: trigger always goes low before enable. Losing authorization mid-pulse
aborts the pulse immediately.
"""
import logging
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("fire-control.controller")

Publish = Callable[[dict], None]


class FireController:
    def __init__(
        self,
        enable_out,
        trigger_out,
        pulse_ms: int,
        max_pulse_ms: int,
        min_interval_s: float,
        publish_event: Publish = lambda e: None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.enable_out = enable_out
        self.trigger_out = trigger_out
        self.pulse_ms = pulse_ms
        self.max_pulse_ms = max_pulse_ms
        self.min_interval_s = min_interval_s
        self.publish_event = publish_event
        self.clock = clock

        self._lock = threading.Lock()
        self._armed = False
        self._auto = False
        self._hub_alive = False     # unknown until we hear the hub; fail closed
        self._link_up = False
        self._enabled = False
        self._abort = threading.Event()
        self._pulsing = False
        self._last_pulse_t = -1e9

    # ----- gate inputs (any thread) ----------------------------------------

    def set_armed(self, on: bool) -> None:
        with self._lock:
            self._armed = bool(on)
        self._apply("armed=%s" % bool(on))

    def set_auto(self, on: bool) -> None:
        with self._lock:
            self._auto = bool(on)
        self._apply("auto=%s" % bool(on))

    def set_hub_alive(self, alive: bool) -> bool:
        """Returns True iff this call disarmed (hub alive -> dead while armed/auto)."""
        disarmed = False
        with self._lock:
            changed = self._hub_alive != alive
            self._hub_alive = alive
            if changed and not alive:
                disarmed = self._armed or self._auto
                # Hub died (alive -> dead). Fail closed: the retained arm/auto
                # values are stale without a hub, so require a fresh arm once
                # it's back. At startup the hub is merely *unknown*; the enable
                # pin stays low but arm state is kept until we hear it.
                self._armed = False
                self._auto = False
        if changed:
            self._apply("hub %s" % ("up" if alive else "DOWN"))
        return disarmed

    def set_link_up(self, up: bool) -> None:
        with self._lock:
            changed = self._link_up != up
            self._link_up = up
        if changed:
            self._apply("mqtt %s" % ("up" if up else "DOWN"))

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def armed(self) -> bool:
        return self._armed

    def _apply(self, reason: str) -> None:
        with self._lock:
            want = (self._armed or self._auto) and self._hub_alive and self._link_up
            if want == self._enabled:
                return
            if want:
                self.enable_out.on()
            else:
                # Trigger before enable, always.
                self._abort.set()
                self.trigger_out.off()
                self.enable_out.off()
            self._enabled = want
        log.info("enable -> %s (%s)", want, reason)
        self.publish_event({"event": "enable", "on": want, "reason": reason})

    # ----- requests (run on the worker thread) ------------------------------

    def fire(self, payload: dict) -> dict:
        """Validate and execute one request; blocks for the pulse. Returns the event."""
        source = str(payload.get("source", "unknown"))
        with self._lock:
            reason = self._reject_reason(source)
            if reason is None:
                self._pulsing = True
                self._last_pulse_t = self.clock()
                self._abort.clear()
        if reason is not None:
            ev = {"event": "rejected", "reason": reason, "source": source}
            log.warning("fire rejected: %s (source=%s)", reason, source)
            self.publish_event(ev)
            return ev

        try:
            pulse_ms = int(payload.get("pulse_ms", self.pulse_ms))
        except (TypeError, ValueError):
            pulse_ms = self.pulse_ms
        pulse_ms = max(1, min(pulse_ms, self.max_pulse_ms))

        start = self.clock()
        try:
            with self._lock:
                # Re-check under the lock: authorization may have dropped
                # between validation and here.
                if not self._enabled:
                    aborted_early = True
                else:
                    aborted_early = False
                    self.trigger_out.on()
            aborted = aborted_early or self._abort.wait(pulse_ms / 1000.0)
        finally:
            self.trigger_out.off()
            with self._lock:
                self._pulsing = False
        held_ms = int(round((self.clock() - start) * 1000))
        ev = {
            "event": "fired",
            "source": source,
            "pulse_ms": pulse_ms,
            "held_ms": held_ms,
            "aborted": bool(aborted),
        }
        log.info("FIRED source=%s pulse=%dms held=%dms aborted=%s",
                 source, pulse_ms, held_ms, aborted)
        self.publish_event(ev)
        return ev

    def _reject_reason(self, source: str) -> Optional[str]:
        if not self._enabled:
            return "not_enabled"
        if self._pulsing:
            return "busy"
        if source == "ui":
            if not (self._armed or self._auto):
                return "not_armed"
        elif not (self._armed and self._auto):
            return "not_armed_and_enabled"
        if self.clock() - self._last_pulse_t < self.min_interval_s:
            return "rate_limited"
        return None

    # ----- shutdown ----------------------------------------------------------

    def shutdown(self) -> None:
        with self._lock:
            self._abort.set()
            self._armed = self._auto = False
            self._enabled = False
        try:
            self.trigger_out.off()
        finally:
            self.enable_out.off()

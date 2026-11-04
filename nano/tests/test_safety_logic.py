"""Hardware-free tests for fire-control authorization and PIR motion tracking.

Run from the repo root:  python3 -m unittest discover -s nano/tests -t .
"""
import threading
import time
import unittest

from nano.src.fire_control.controller import FireController
from nano.src.sensor_wakeup.main import MotionTracker


class FakePin:
    def __init__(self, name, log):
        self.name, self.log, self.value = name, log, False

    def on(self):
        self.value = True
        self.log.append((self.name, True))

    def off(self):
        self.value = False
        self.log.append((self.name, False))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(**kw):
    log = []
    en, tr = FakePin("enable", log), FakePin("trigger", log)
    events = []
    clock = kw.pop("clock", Clock())
    ctl = FireController(en, tr, pulse_ms=kw.pop("pulse_ms", 1), max_pulse_ms=50,
                         min_interval_s=0.35, publish_event=events.append, clock=clock)
    return ctl, en, tr, events, log, clock


def ready(ctl, armed=True, auto=False):
    ctl.set_link_up(True)
    ctl.set_hub_alive(True)
    ctl.set_armed(armed)
    ctl.set_auto(auto)


class FireControllerTests(unittest.TestCase):
    def test_starts_disabled_and_fails_closed_until_hub_and_link(self):
        ctl, en, tr, ev, _, _ = make()
        ctl.set_armed(True)
        self.assertFalse(en.value)                      # no hub, no link
        ctl.set_link_up(True)
        self.assertFalse(en.value)                      # still no hub
        ctl.set_hub_alive(True)
        self.assertTrue(en.value)
        self.assertEqual(ctl.fire({"source": "ui"})["event"], "fired")

    def test_ui_needs_armed_or_auto(self):
        ctl, en, *_ = make()
        ready(ctl, armed=False, auto=False)
        self.assertEqual(ctl.fire({"source": "ui"})["reason"], "not_enabled")
        ctl.set_auto(True)
        self.assertEqual(ctl.fire({"source": "ui"})["event"], "fired")

    def test_auto_sentry_needs_armed_and_auto(self):
        ctl, *_ , clock = make()
        ready(ctl, armed=True, auto=False)
        self.assertEqual(ctl.fire({"source": "auto-sentry"})["reason"], "not_armed_and_enabled")
        ctl.set_auto(True)
        self.assertEqual(ctl.fire({"source": "auto-sentry"})["event"], "fired")

    def test_unknown_source_gets_strictest_rule(self):
        ctl, *_ = make()
        ready(ctl, armed=True, auto=False)
        self.assertEqual(ctl.fire({})["reason"], "not_armed_and_enabled")

    def test_rate_limit(self):
        ctl, en, tr, ev, log, clock = make()
        ready(ctl)
        self.assertEqual(ctl.fire({"source": "ui"})["event"], "fired")
        clock.t += 0.2
        self.assertEqual(ctl.fire({"source": "ui"})["reason"], "rate_limited")
        clock.t += 0.2
        self.assertEqual(ctl.fire({"source": "ui"})["event"], "fired")

    def test_pulse_clamped(self):
        ctl, *_ = make()
        ready(ctl)
        self.assertEqual(ctl.fire({"source": "ui", "pulse_ms": 99999})["pulse_ms"], 50)
        ctl2, *_ = make()
        ready(ctl2)
        self.assertEqual(ctl2.fire({"source": "ui", "pulse_ms": "junk"})["pulse_ms"], 1)

    def test_trigger_low_after_pulse(self):
        ctl, en, tr, ev, log, _ = make()
        ready(ctl)
        ctl.fire({"source": "ui"})
        self.assertFalse(tr.value)
        self.assertIn(("trigger", True), log)

    def test_disarm_mid_pulse_aborts_trigger_before_enable(self):
        ctl, en, tr, ev, log, _ = make(pulse_ms=50, clock=time.monotonic)
        ready(ctl)
        result = {}
        t = threading.Thread(target=lambda: result.update(ctl.fire({"source": "ui"})))
        t.start()
        deadline = time.monotonic() + 1
        while not tr.value and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertTrue(tr.value, "pulse never started")
        log.clear()
        ctl.set_armed(False)
        t.join(1)
        self.assertTrue(result["aborted"])
        self.assertFalse(tr.value)
        self.assertFalse(en.value)
        # trigger went low before enable did
        first_trigger_off = log.index(("trigger", False))
        first_enable_off = log.index(("enable", False))
        self.assertLess(first_trigger_off, first_enable_off)
        self.assertLess(result["held_ms"], 40)

    def test_hub_death_disarms_and_requires_rearm(self):
        ctl, en, *_ = make()
        ready(ctl, armed=True, auto=True)
        self.assertTrue(ctl.set_hub_alive(False))    # reports the disarm
        self.assertFalse(en.value)
        self.assertFalse(ctl.armed)
        ctl.set_hub_alive(True)
        self.assertFalse(en.value, "must not re-enable without a fresh arm")
        ctl.set_armed(True)
        self.assertTrue(en.value)

    def test_startup_unknown_hub_keeps_retained_arm(self):
        ctl, en, *_ = make()
        ctl.set_link_up(True)
        ctl.set_armed(True)          # retained value delivered before hub is heard
        self.assertFalse(ctl.set_hub_alive(False))   # no transition -> no disarm
        self.assertTrue(ctl.armed)
        self.assertFalse(en.value)
        ctl.set_hub_alive(True)
        self.assertTrue(en.value)

    def test_mqtt_loss_drops_enable(self):
        ctl, en, *_ = make()
        ready(ctl)
        ctl.set_link_up(False)
        self.assertFalse(en.value)
        self.assertEqual(ctl.fire({"source": "ui"})["reason"], "not_enabled")

    def test_shutdown_all_low(self):
        ctl, en, tr, *_ = make()
        ready(ctl)
        ctl.shutdown()
        self.assertFalse(en.value)
        self.assertFalse(tr.value)


class MotionTrackerTests(unittest.TestCase):
    def test_continuous_high_stays_active(self):
        m = MotionTracker(inactive_after_s=2.0)
        self.assertIs(m.update(True, 0.0), True)
        for i in range(1, 100):                    # 5 s held high at 20 Hz
            self.assertIsNone(m.update(True, i * 0.05))
        self.assertTrue(m.active)

    def test_goes_inactive_after_low_for_timeout(self):
        m = MotionTracker(inactive_after_s=2.0)
        m.update(True, 0.0)
        self.assertIsNone(m.update(False, 1.9))
        self.assertIs(m.update(False, 2.1), False)
        self.assertIsNone(m.update(False, 3.0))    # published once
        self.assertIs(m.update(True, 3.1), True)   # re-announced


if __name__ == "__main__":
    unittest.main()

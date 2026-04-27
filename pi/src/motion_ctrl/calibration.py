"""Servo calibration: normalized aim [-1, 1] -> microsecond pulse width.

Phase 1 ships hardcoded SG90-ish defaults. Phase 3 will replace this with
load/save from /var/lib/turret/motion_ctrl_calibration.json + a Settings
panel.

Mapping:
    a = -axis if invert else axis
    span = (hi - lo) / 2
    us = clamp(center + a * span, lo, hi)
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class AxisCal:
    min_us: int
    max_us: int
    center_us: int
    invert: bool = False

    def to_us(self, axis: float) -> int:
        a = -axis if self.invert else axis
        if a < -1.0:
            a = -1.0
        elif a > 1.0:
            a = 1.0
        span = (self.max_us - self.min_us) / 2.0
        raw = self.center_us + a * span
        if raw < self.min_us:
            return self.min_us
        if raw > self.max_us:
            return self.max_us
        return int(raw)


@dataclass(frozen=True)
class Calibration:
    pan: AxisCal
    tilt: AxisCal
    max_slew_us_per_tick: int
    tick_hz: int
    state_publish_hz: int


# Defaults documented in motor_control_plan.md §6.3. Conservative full
# range; real values get measured in phase 3.
DEFAULT = Calibration(
    pan=AxisCal(min_us=1000, max_us=2000, center_us=1500, invert=False),
    tilt=AxisCal(min_us=1000, max_us=2000, center_us=1500, invert=False),
    max_slew_us_per_tick=40,
    tick_hz=50,
    state_publish_hz=5,
)


def slew(current_us: int, target_us: int, max_step: int) -> int:
    """Move current toward target by at most max_step microseconds."""
    delta = target_us - current_us
    if delta > max_step:
        return current_us + max_step
    if delta < -max_step:
        return current_us - max_step
    return target_us


def us_to_axis(us: int, cal: AxisCal) -> float:
    """Inverse of AxisCal.to_us, for the state publish payload."""
    span = (cal.max_us - cal.min_us) / 2.0
    if span == 0:
        return 0.0
    a = (us - cal.center_us) / span
    if cal.invert:
        a = -a
    if a < -1.0:
        return -1.0
    if a > 1.0:
        return 1.0
    return a

"""Servo calibration: normalized aim [-1, 1] -> microsecond pulse width.

Mapping:
    a = -axis if invert else axis
    span = (hi - lo) / 2
    us = clamp(center + a * span, lo, hi)

Phase 3: persisted at CALIBRATION_PATH and mirrored on
turret/motion_ctrl/calibration (retained). The Settings UI publishes; this
module loads + validates.
"""
import json
import logging
import os
import tempfile
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
    pan=AxisCal(min_us=1000, max_us=2000, center_us=1500, invert=True),
    tilt=AxisCal(min_us=1000, max_us=2000, center_us=1500, invert=True),
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


# Dedicated user-owned dir (created by nano/scripts/host_prep.sh) so
# /var/lib/turret itself can stay root-owned.
CALIBRATION_PATH = "/var/lib/turret/motion_ctrl/calibration.json"

# Servo-safe absolute bounds; protects from typos in the persisted payload.
_US_MIN = 500
_US_MAX = 2500
_SLEW_MIN = 1
_SLEW_MAX = 500

log = logging.getLogger("motion-ctrl.calibration")


def _validate_axis(d: dict, name: str) -> AxisCal:
    if not isinstance(d, dict):
        raise ValueError(f"{name}: not an object")
    try:
        lo = int(d["min_us"])
        hi = int(d["max_us"])
        ce = int(d["center_us"])
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{name}: missing/invalid us field ({e})")
    if not (_US_MIN <= lo < ce < hi <= _US_MAX):
        raise ValueError(
            f"{name}: require {_US_MIN} <= min_us < center_us < max_us <= {_US_MAX}, "
            f"got min={lo} center={ce} max={hi}"
        )
    return AxisCal(min_us=lo, max_us=hi, center_us=ce, invert=bool(d.get("invert", False)))


def axis_to_dict(a: AxisCal) -> dict:
    return {"min_us": a.min_us, "max_us": a.max_us, "center_us": a.center_us, "invert": a.invert}


def to_dict(cal: Calibration) -> dict:
    return {
        "pan": axis_to_dict(cal.pan),
        "tilt": axis_to_dict(cal.tilt),
        "max_slew_us_per_tick": cal.max_slew_us_per_tick,
        "tick_hz": cal.tick_hz,
        "state_publish_hz": cal.state_publish_hz,
    }


def from_dict(d: dict, base: Calibration = DEFAULT) -> Calibration:
    """Validate + coerce a persisted/MQTT payload into a Calibration.

    Missing top-level fields fall back to `base` so partial PUTs work
    (e.g. the UI sliders only touch axis bounds).
    """
    pan = _validate_axis(d["pan"], "pan") if "pan" in d else base.pan
    tilt = _validate_axis(d["tilt"], "tilt") if "tilt" in d else base.tilt
    try:
        slew_us = int(d.get("max_slew_us_per_tick", base.max_slew_us_per_tick))
    except (TypeError, ValueError):
        raise ValueError("max_slew_us_per_tick: not an int")
    if not (_SLEW_MIN <= slew_us <= _SLEW_MAX):
        raise ValueError(
            f"max_slew_us_per_tick: require {_SLEW_MIN}..{_SLEW_MAX}, got {slew_us}"
        )
    return Calibration(
        pan=pan,
        tilt=tilt,
        max_slew_us_per_tick=slew_us,
        tick_hz=base.tick_hz,
        state_publish_hz=base.state_publish_hz,
    )


def load(path: str = CALIBRATION_PATH) -> Calibration:
    """Read calibration from disk; return DEFAULT if missing or invalid."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return DEFAULT
    except (OSError, ValueError) as e:
        log.warning("calibration load failed (%s); using defaults", e)
        return DEFAULT
    try:
        return from_dict(data)
    except ValueError as e:
        log.warning("calibration validation failed (%s); using defaults", e)
        return DEFAULT


def save(cal: Calibration, path: str = CALIBRATION_PATH) -> None:
    """Atomically persist calibration to JSON. Creates parent dir if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".cal-", dir=parent or None)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(to_dict(cal), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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

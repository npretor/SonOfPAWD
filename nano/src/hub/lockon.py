"""Auto-sentry lock-on gate.

Pure state machine. No MQTT, no I/O. Hub feeds it detections + aim state and
gets back an optional FireDecision. See motor_control_plan.md §Phase 4.

States:
    IDLE        — no recent qualifying detections
    ACQUIRING   — 1..N-1 in the rolling buffer
    LOCKED      — fire decision was just emitted (transient; persists in
                  snapshots for LOCKED_HOLD_S so the UI can flash it)
    COOLDOWN    — suppress further fires for COOLDOWN_S

Reset to IDLE on any of:
    - target class changes
    - centroid jumps further than CENTROID_TOL from the buffer mean
    - more than STALE_S elapsed since the last passing detection
    - feed() called with target_class=None (caller should reset on mode exit)
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class _Entry:
    ts: float
    cx: float
    cy: float
    score: float


@dataclass
class FireDecision:
    target: str
    score: float           # mean score across the lock buffer
    centroid: Tuple[float, float]
    ts: float


@dataclass
class _AimSample:
    ts: float
    pan_us: int
    tilt_us: int


class LockonGate:
    # Tunables. Class-level so tests can patch and the hub can later expose
    # them via hub/config without touching this module's interface.
    N = 4
    WINDOW_S = 1.5
    SCORE_FIRE_MIN = 0.65
    STALE_S = 0.7
    CENTROID_TOL = 0.08
    SETTLED_TOL_US = 30
    COOLDOWN_S = 5.0
    LOCKED_HOLD_S = 0.6  # how long snapshot() reports "LOCKED" after firing

    def __init__(self) -> None:
        self._state: str = "IDLE"
        self._target: Optional[str] = None
        self._buffer: List[_Entry] = []
        self._last_fire_ts: float = -1e9
        self._aim_prev: Optional[_AimSample] = None
        self._aim_last: Optional[_AimSample] = None

    # ----- inputs --------------------------------------------------------

    def note_aim_state(self, pan_us: int, tilt_us: int, ts: float) -> None:
        """Hub calls this on every motion_ctrl/state message."""
        self._aim_prev = self._aim_last
        self._aim_last = _AimSample(ts=ts, pan_us=int(pan_us), tilt_us=int(tilt_us))

    def reset(self) -> None:
        """Caller-driven reset. Use on mode exit or motion_ctrl disable."""
        self._state = "IDLE"
        self._buffer.clear()

    def tick(self, now: float) -> None:
        """Periodic time-based transitions (cooldown expiry, staleness)."""
        if self._state == "COOLDOWN" and now - self._last_fire_ts >= self.COOLDOWN_S:
            self._state = "IDLE"
            self._buffer.clear()
            return
        if self._state == "ACQUIRING" and self._buffer:
            if now - self._buffer[-1].ts > self.STALE_S:
                self._state = "IDLE"
                self._buffer.clear()

    def feed(
        self,
        detections: List[dict],
        target_class: Optional[str],
        now: float,
    ) -> Optional[FireDecision]:
        """Consume one frame's worth of detections; maybe emit a fire decision.

        `detections` is the canonical list-of-dicts shape; pass-through from the
        hub. `target_class` is the user-selected class to fire on. `now` is a
        monotonic-or-wall-clock float in seconds (any consistent source).
        """
        # Mode/target gone? Drop everything.
        if not target_class:
            self.reset()
            self._target = None
            return None

        if target_class != self._target:
            self._target = target_class
            self._state = "IDLE"
            self._buffer.clear()

        # Cooldown takes precedence.
        if self._state == "COOLDOWN":
            if now - self._last_fire_ts < self.COOLDOWN_S:
                return None
            # Cooldown elapsed; resume normal logic.
            self._state = "IDLE"

        # Pick the best matching detection from this frame.
        best = self._best_match(detections, target_class)
        if best is None:
            # No qualifying detection this frame. Staleness handled in tick().
            return None
        cx, cy, score = best

        # Drop the buffer entirely if too old to associate with this frame.
        if self._buffer and (now - self._buffer[-1].ts > self.STALE_S):
            self._buffer.clear()
            self._state = "IDLE"

        # Trim window-aged entries from the front.
        cutoff = now - self.WINDOW_S
        self._buffer = [e for e in self._buffer if e.ts >= cutoff]

        # Centroid-agreement check against the rolling mean.
        if self._buffer:
            mean_cx = sum(e.cx for e in self._buffer) / len(self._buffer)
            mean_cy = sum(e.cy for e in self._buffer) / len(self._buffer)
            if math.hypot(cx - mean_cx, cy - mean_cy) > self.CENTROID_TOL:
                # Target moved too far — treat as a new acquisition.
                self._buffer.clear()

        self._buffer.append(_Entry(ts=now, cx=cx, cy=cy, score=score))
        self._state = "ACQUIRING"

        # Lock condition.
        if len(self._buffer) < self.N:
            return None
        if not self._aim_settled(now):
            return None

        avg_score = sum(e.score for e in self._buffer) / len(self._buffer)
        avg_cx = sum(e.cx for e in self._buffer) / len(self._buffer)
        avg_cy = sum(e.cy for e in self._buffer) / len(self._buffer)
        self._last_fire_ts = now
        self._state = "COOLDOWN"
        self._buffer.clear()
        return FireDecision(
            target=target_class,
            score=avg_score,
            centroid=(avg_cx, avg_cy),
            ts=now,
        )

    # ----- helpers -------------------------------------------------------

    def _best_match(
        self,
        detections: List[dict],
        target_class: str,
    ) -> Optional[Tuple[float, float, float]]:
        best = None
        for d in detections or []:
            if not isinstance(d, dict):
                continue
            if d.get("class") != target_class:
                continue
            try:
                score = float(d.get("score", 0.0))
            except (TypeError, ValueError):
                continue
            if score < self.SCORE_FIRE_MIN:
                continue
            bbox = d.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                x0, y0, x1, y1 = (float(v) for v in bbox)
            except (TypeError, ValueError):
                continue
            cx = (x0 + x1) / 2.0
            cy = (y0 + y1) / 2.0
            if best is None or score > best[2]:
                best = (cx, cy, score)
        return best

    def _aim_settled(self, now: float) -> bool:
        """Permissive: True if motion-ctrl hasn't reported state yet (caller's
        problem if they auto-fire without it). Otherwise require the last two
        state samples to be close together AND recent."""
        if self._aim_last is None:
            return True
        if now - self._aim_last.ts > 1.5:
            # State stream went stale — be permissive (system isn't actively
            # commanding motion if we're not getting updates).
            return True
        if self._aim_prev is None:
            return False  # need two samples to measure delta
        d = abs(self._aim_last.pan_us - self._aim_prev.pan_us) + \
            abs(self._aim_last.tilt_us - self._aim_prev.tilt_us)
        return d <= self.SETTLED_TOL_US

    # ----- snapshot for UI ------------------------------------------------

    def snapshot(self, now: float) -> dict:
        out = {
            "state": self._state,
            "target": self._target,
            "count": len(self._buffer),
            "of": self.N,
        }
        if self._state == "COOLDOWN":
            elapsed = now - self._last_fire_ts
            if elapsed < self.LOCKED_HOLD_S:
                out["state"] = "LOCKED"
            out["cooldown_remaining"] = max(0.0, self.COOLDOWN_S - elapsed)
        return out

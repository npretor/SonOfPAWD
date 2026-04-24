"""HubState: the hub's in-memory model.

All mutations run on the GLib main loop thread; paho callbacks marshal via
GLib.idle_add before touching this. Keep this module free of I/O so it stays
unit-testable without a broker or hardware.
"""
from dataclasses import dataclass, field
from typing import Dict

from nano.src.hub.modes import Mode


@dataclass
class HubState:
    mode: Mode = Mode.IDLE
    target_class: str = "cat"  # cat | raccoon | rat | deer; garden pest turret
    recorder_armed: bool = False
    last_motion_ts: float = 0.0
    # "host/service" -> "up" | "down"
    peers: Dict[str, str] = field(default_factory=dict)
    # IR-merge inputs (see ir_merge.py in Step C; tracked here so Step B can
    # already aggregate them into turret/hub/state).
    ir_recording: bool = False
    ir_manual: bool = False
    ir_trigger: bool = False

    def ir_on(self) -> bool:
        return self.ir_recording or self.ir_manual or self.ir_trigger

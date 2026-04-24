"""Nano telemetry: temp, CPU %, RAM %, disk free on CLIPS_DIR.

No extra dependencies -- reads /sys and /proc directly.
"""
import glob
import logging
import shutil
from typing import Optional

from common.config.hosts import CLIPS_DIR

log = logging.getLogger(__name__)


def _pick_thermal_path() -> str:
    """Prefer a CPU thermal zone; fall back to thermal_zone0.

    Jetson Nano exposes several zones (AO-therm, CPU-therm, GPU-therm, ...).
    Picking CPU-therm when available keeps the metric useful.
    """
    for type_path in sorted(glob.glob("/sys/class/thermal/thermal_zone*/type")):
        try:
            with open(type_path) as f:
                zone_name = f.read().strip()
        except OSError:
            continue
        if "cpu" in zone_name.lower():
            return type_path.replace("/type", "/temp")
    return "/sys/class/thermal/thermal_zone0/temp"


class TelemetryReader:
    def __init__(self):
        self._thermal_path = _pick_thermal_path()
        self._prev_cpu = None  # (total, idle) from last tick
        log.info("telemetry thermal path: %s", self._thermal_path)

    # ---- individual readers --------------------------------------------

    def _read_temp_c(self) -> Optional[float]:
        try:
            with open(self._thermal_path) as f:
                return int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            return None

    def _read_cpu_counters(self):
        with open("/proc/stat") as f:
            parts = f.readline().split()
        # parts = ['cpu', user, nice, system, idle, iowait, irq, softirq, steal, ...]
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return total, idle

    def _read_cpu_pct(self) -> float:
        try:
            total, idle = self._read_cpu_counters()
        except (OSError, ValueError):
            return 0.0
        if self._prev_cpu is None:
            self._prev_cpu = (total, idle)
            return 0.0
        prev_total, prev_idle = self._prev_cpu
        self._prev_cpu = (total, idle)
        dt = total - prev_total
        di = idle - prev_idle
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (dt - di) / dt))

    def _read_ram_pct(self) -> float:
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, _, v = line.partition(":")
                    info[k.strip()] = int(v.strip().split()[0])  # kB
            total = info["MemTotal"]
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            return max(0.0, min(100.0, 100.0 * (1 - avail / total)))
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return 0.0

    def _read_disk(self):
        try:
            u = shutil.disk_usage(CLIPS_DIR)
            return u.free, u.total
        except OSError:
            return 0, 0

    # ---- main sampler --------------------------------------------------

    def sample(self) -> dict:
        free, total = self._read_disk()
        return {
            "temp_c": self._read_temp_c(),
            "cpu_pct": round(self._read_cpu_pct(), 1),
            "ram_pct": round(self._read_ram_pct(), 1),
            "disk_free_bytes": free,
            "disk_total_bytes": total,
        }

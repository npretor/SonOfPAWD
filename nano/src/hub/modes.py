"""Mode enum for the hub. String values match the MQTT payload field."""
from enum import Enum


class Mode(str, Enum):
    IDLE = "idle"
    MONITOR = "monitor"
    TRAILCAM = "trailcam"
    AUTO_AIM = "auto-aim"
    AUTO_SENTRY = "auto-sentry"


VALID_MODES = {m.value for m in Mode}

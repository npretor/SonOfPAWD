"""MQTT topic names. Do not let magic strings leak into service code."""

# --- Existing contract ---
TOPIC_MOTION = "turret/motion"
TOPIC_IR_SET = "turret/ir/set"
TOPIC_CAMERA_STATUS = "turret/camera/status"
TOPIC_RECORDER_ARM = "turret/recorder/arm"
TOPIC_RECORDER_EVENT = "turret/recorder/event"

TOPIC_HEALTH_ALL = "turret/+/health"
TOPIC_HUB_COMMAND_ALL = "turret/hub/command/+"

# --- Hub contract (core_service_implimentation.md §2.3) ---
TOPIC_MODE_SET = "turret/mode/set"
TOPIC_MODE_CURRENT = "turret/mode/current"      # retained
TOPIC_HUB_STATE = "turret/hub/state"            # retained
TOPIC_TELEMETRY = "turret/nano/telemetry"
TOPIC_HUB_CONFIG = "turret/hub/config"          # retained — UI <-> hub settings

TOPIC_IR_MANUAL = "turret/ir/manual"

TOPIC_SNAPSHOT_REQ = "turret/snapshot/request"
TOPIC_SNAPSHOT_EVT = "turret/snapshot/event"

TOPIC_INFER_ENABLE = "turret/inference/enable"  # retained
TOPIC_INFER_DETECT = "turret/inference/detection"

TOPIC_MOTION_CTRL_ENABLE = "turret/motion_ctrl/enable"  # retained
TOPIC_MOTION_CTRL_AIM = "turret/motion_ctrl/aim"
TOPIC_MOTION_CTRL_STATE = "turret/motion_ctrl/state"
TOPIC_MOTION_CTRL_CALIBRATION = "turret/motion_ctrl/calibration"  # retained

TOPIC_FIRE_ENABLE = "turret/fire/enable"        # retained — auto-fire (mode-driven)
TOPIC_FIRE_ARMED = "turret/fire/armed"          # retained — UI-driven manual arm
TOPIC_FIRE_REQUEST = "turret/fire/request"
TOPIC_FIRE_EVENT = "turret/fire/event"

TOPIC_ALERT_USER = "turret/alert"


def topic_health(host_tag: str) -> str:
    return f"turret/{host_tag}/health"


def topic_hub_command(service: str) -> str:
    return f"turret/hub/command/{service}"

"""MQTT topic names. Do not let magic strings leak into service code."""

TOPIC_MOTION = "turret/motion"
TOPIC_IR_SET = "turret/ir/set"
TOPIC_CAMERA_STATUS = "turret/camera/status"
TOPIC_RECORDER_ARM = "turret/recorder/arm"
TOPIC_RECORDER_EVENT = "turret/recorder/event"

TOPIC_HEALTH_ALL = "turret/+/health"
TOPIC_HUB_COMMAND_ALL = "turret/hub/command/+"


def topic_health(host_tag: str) -> str:
    return f"turret/{host_tag}/health"


def topic_hub_command(service: str) -> str:
    return f"turret/hub/command/{service}"

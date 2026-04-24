"""alert_user shim. Logs + publishes; real notification transport comes later."""
import logging

from common.messaging.client import MqttClient
from common.messaging.topics import TOPIC_ALERT_USER

log = logging.getLogger("hub.alerts")


def alert_user(level: str, message: str, *, mqtt: MqttClient) -> None:
    log.warning("ALERT [%s] %s", level, message)
    mqtt.publish(TOPIC_ALERT_USER, {"level": level, "message": message})

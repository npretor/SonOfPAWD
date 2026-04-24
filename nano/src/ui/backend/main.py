#!/usr/bin/env python3
"""ui-backend entry point.

Builds the Flask app, starts the MQTT bridge, and serves HTTP + Socket.IO.

H.2 scope: /api/health plus WebSocket emission of hub_state, mode_current,
telemetry, health, recorder_event. Command endpoints (mode, fire, aim,
settings, logs) arrive in H.4+.
"""
import logging
import signal
import sys

from nano.src.ui.backend.app import create_app
from nano.src.ui.backend.mqtt_bridge import MqttBridge
from nano.src.ui.backend.routes import register_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ui.main")

HOST = "0.0.0.0"
PORT = 5000


def main() -> int:
    app, socketio = create_app()
    bridge = MqttBridge(socketio)
    bridge.start()
    register_routes(app, bridge)

    def shutdown(signum, _frame):
        log.info("signal %d received, shutting down", signum)
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("ui-backend listening on http://%s:%d", HOST, PORT)
    # socketio.run() picks its server based on async_mode. For threading mode
    # that's Werkzeug; it's fine for LAN-only load and sidesteps the
    # waitress-vs-socketio long-poll plumbing. Newer Flask-SocketIO refuses
    # Werkzeug without an opt-in flag; older versions don't know the kwarg.
    try:
        socketio.run(app, host=HOST, port=PORT, allow_unsafe_werkzeug=True)
    except TypeError:
        socketio.run(app, host=HOST, port=PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Flask app factory + SocketIO instance.

The app is built as two separate modules so that:
  - main.py can construct + wire + serve without circular imports
  - mqtt_bridge.py can grab the SocketIO handle and emit into it

We use threading async_mode (no eventlet/gevent C deps). Under Werkzeug this
serves Socket.IO long-polling fine; telemetry at 5 s cadence doesn't need
websocket transport. If a browser negotiates websocket, Flask-SocketIO + the
optional `simple-websocket` package handle it transparently.

Static bundle: `vite build` writes to `nano/src/ui/static/`. This module
serves that directory at `/`, with SPA fallback to `index.html` so
React-Router client routes (e.g. /settings) don't 404 on hard refresh.
"""
import logging
import os

from flask import Flask, jsonify, send_from_directory
from flask_socketio import SocketIO

log = logging.getLogger("ui.app")

_STATIC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "static")
)


def create_app():
    app = Flask(__name__)
    # LAN-only, no login, no cookies persisted; still set something sane.
    app.config["SECRET_KEY"] = "sonofpawd-lan-only"

    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins="*",   # Vite dev server runs on a different port
        logger=False,
        engineio_logger=False,
    )

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "service": "ui-backend"})

    # Static SPA. Registered after /api/* so Flask's routing picks the more
    # specific API routes first. Added in H.3.
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def serve_spa(path):
        index = os.path.join(_STATIC_DIR, "index.html")
        if not os.path.isfile(index):
            return (
                "UI bundle not built. Run:\n"
                "  cd nano/src/ui/frontend && npm install && npm run build\n",
                503,
                {"Content-Type": "text/plain"},
            )
        full = os.path.join(_STATIC_DIR, path)
        if path and os.path.isfile(full):
            return send_from_directory(_STATIC_DIR, path)
        return send_from_directory(_STATIC_DIR, "index.html")

    return app, socketio

"""Single source of truth for turret config.

Everything runs on the Jetson Nano (the Pi was retired Nov 2026).
"""

# --- Host (mDNS name; fall back to the IP if avahi is flaky) ---
NANO_HOST = "dlinano-hqcam.local"

# --- MQTT (broker runs on Nano) ---
MQTT_HOST = NANO_HOST
MQTT_PORT = 1883
MQTT_KEEPALIVE_S = 30

# --- MQTT auth. Broker requires creds; every client loads the password from this file. ---
MQTT_USERNAME = "turret"
MQTT_PASSWORD_FILE = "/etc/turret/mqtt.password"

# --- RTSP: the Nano's camera-pipeline publishes into the local MediaMTX,
# which serves it to recorder / inference / UI (WebRTC). ---
RTSP_PORT = 8554
RTSP_PATH = "/live"
RTSP_URL = f"rtsp://127.0.0.1:{RTSP_PORT}{RTSP_PATH}"

# --- Camera encode settings ---
CAMERA_WIDTH = 1920
CAMERA_HEIGHT = 1080
CAMERA_FPS = 30
CAMERA_BITRATE = 6_000_000  # 6 Mbps H.264

# --- Nano camera (IMX219 NoIR on CAM1, via nvarguscamerasrc) ---
CAMERA_SENSOR_ID = 0     # Argus index; only one sensor is enumerated
# Always pin the mode. Mode 2 = 1920x1080@30 (center crop). Letting Argus pick
# a full-res mode has exhausted NVMM and panicked this Nano before.
CAMERA_SENSOR_MODE = 2
CAMERA_FLIP = 0          # nvvidconv flip-method; 0 = none, 2 = rotate 180
# Runtime dir (systemd RuntimeDirectory=) for the 'healthy' marker that
# nano/scripts/camera_recover.sh reads.
CAMERA_STATE_DIR = "/run/camera-pipeline"

# --- 40-pin header GPIO, BCM numbering (Jetson.GPIO BCM mode, nano/src/hw/gpio.py).
# Same physical pins as on the Pi: BCM17=pin 11, 27=13, 24=18, 22=15, 23=16.
# All outputs feed optoisolators with pull-downs on their inputs. ---
PIR_PIN = 17
IR_LED_PIN = 27
# Servo PSU enable (motor_control_plan.md §3). Active high; gates the
# dedicated servo supply via MOSFET. Must be low at boot so servos are
# never energized into an unknown commanded position.
SERVO_POWER_PIN = 24

# --- Servo controller (Arduino over USB). ---
# Stable path is provided by nano/scripts/udev/99-turret-arduino.rules
# (installed by nano/scripts/host_prep.sh). For dev without the rule, override
# to /dev/ttyACM0.
SERVO_SERIAL_DEV = "/dev/turret-arduino"
SERVO_SERIAL_BAUD = 115200

# --- Fire control (core_service_implimentation.md §3.2) ---
FIRE_ENABLE_PIN = 22
FIRE_TRIGGER_PIN = 23
FIRE_PULSE_MS = 250          # default valve-open time when a request omits pulse_ms
FIRE_MAX_PULSE_MS = 1000     # hard clamp on any requested pulse
# Slightly under the UI backend's 0.4 s limit so network jitter can't make a
# legitimately rate-limited UI request bounce here.
FIRE_MIN_INTERVAL_S = 0.35

# --- PIR motion debounce ---
PIR_INACTIVE_AFTER_S = 2.0

# --- Recording (Nano) ---
TRAILING_SECONDS = 10
CLIPS_DIR = "/var/lib/turret/clips"

# --- Hub telemetry cadence (seconds between turret/nano/telemetry publishes) ---
TELEMETRY_PERIOD_S = 5

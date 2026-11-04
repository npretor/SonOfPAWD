#!/usr/bin/env bash
# camera-pipeline recovery helper. Runs as ROOT via ExecStopPost=+ after every
# stop of camera-pipeline.service. Installed root-owned to
# /usr/local/lib/turret/camera-recover.sh by `make -C deploy install-camera-unit`.
# Never point the unit at the copy in the (user-writable) repo, since that would
# let the user account run arbitrary code as root.
#
# Policy (systemd sets SERVICE_RESULT / EXIT_CODE / EXIT_STATUS, v232+):
#   clean stop (success)                  -> nothing
#   exit 1 / 4 (other / publish failure)  -> nothing; systemd just restarts us
#   exit 3, signal, core-dump, timeout    -> Argus may be wedged:
#       restart nvargus-daemon; after MAX_FAILS consecutive such failures,
#       reboot. At most one camera-triggered reboot per REBOOT_MIN_INTERVAL_S;
#       once that's spent we keep restarting the daemon with backoff instead
#       (e.g. ribbon unplugged: don't reboot-loop the turret).
#
# The service writes $USER_STATE_DIR/healthy after streaming stably; seeing it
# resets the failure counter.
#
# Testing: DRY_RUN=1 prints actions instead of running them; all paths are
# overridable via env.
set -u

USER_STATE_DIR="${USER_STATE_DIR:-/run/camera-pipeline}"          # owned by the service user
ROOT_STATE_DIR="${ROOT_STATE_DIR:-/run/turret-camera-recover}"    # root-only, cleared on boot
REBOOT_LOG_DIR="${REBOOT_LOG_DIR:-/var/lib/turret-camera-recover}" # root-only, persistent
MAX_FAILS="${MAX_FAILS:-5}"
REBOOT_MIN_INTERVAL_S="${REBOOT_MIN_INTERVAL_S:-3600}"
BACKOFF_S="${BACKOFF_S:-30}"
DRY_RUN="${DRY_RUN:-0}"

RESULT="${SERVICE_RESULT:-}"
CODE="${EXIT_CODE:-}"
STATUS="${EXIT_STATUS:-}"

log() { echo "camera-recover: $*"; }
act() { if [ "$DRY_RUN" = 1 ]; then log "DRY_RUN: $*"; else "$@"; fi; }

# Root-owned dirs only. Files we *write* must never live in a user-writable
# dir, or a symlink planted there would redirect a root write.
umask 077
mkdir -p "$ROOT_STATE_DIR" "$REBOOT_LOG_DIR"
COUNT_FILE="$ROOT_STATE_DIR/consecutive_failures"
REBOOT_FILE="$REBOOT_LOG_DIR/last_reboot_epoch"

# Healthy marker: only test and unlink it. rm on a symlink removes the link itself.
if [ -e "$USER_STATE_DIR/healthy" ] || [ -L "$USER_STATE_DIR/healthy" ]; then
    rm -f "$USER_STATE_DIR/healthy"
    echo 0 > "$COUNT_FILE"
fi

suspect=0
case "$RESULT" in
    success) ;;
    exit-code) [ "$STATUS" = 3 ] && suspect=1 ;;
    signal|core-dump|timeout|watchdog) suspect=1 ;;
    *) ;;  # resources, start-limit-hit, etc.: nothing Argus-specific to do
esac

if [ "$suspect" = 0 ]; then
    log "stop result=$RESULT code=$CODE status=$STATUS: no Argus recovery needed"
    exit 0
fi

n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))
echo "$n" > "$COUNT_FILE"
log "Argus-suspect failure #$n (result=$RESULT code=$CODE status=$STATUS)"

if [ "$n" -ge "$MAX_FAILS" ]; then
    now=$(date +%s)
    last=$(cat "$REBOOT_FILE" 2>/dev/null || echo 0)
    case "$last" in ''|*[!0-9]*) last=0 ;; esac
    if [ $((now - last)) -ge "$REBOOT_MIN_INTERVAL_S" ]; then
        echo "$now" > "$REBOOT_FILE"
        if [ "$last" = 0 ]; then last_s=never; else last_s=$(date -d "@$last" '+%F %T'); fi
        log "$n consecutive camera failures; REBOOTING (last camera reboot: $last_s)"
        act systemctl --no-block reboot
        exit 0
    fi
    log "$n consecutive camera failures, but last camera reboot was $((now - last))s ago" \
        "(< ${REBOOT_MIN_INTERVAL_S}s); not rebooting. Check the sensor/ribbon. Backing off ${BACKOFF_S}s"
    sleep "$BACKOFF_S"
fi

# --no-block: a blocking restart from inside our own stop job can deadlock,
# since we're ordered After=nvargus-daemon.
log "restarting nvargus-daemon"
act systemctl --no-block restart nvargus-daemon
exit 0

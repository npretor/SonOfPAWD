#!/usr/bin/env bash
# Installs MediaMTX to /opt/mediamtx and copies the config from this repo.
# Idempotent: re-runs upgrade the binary in place.
set -euo pipefail

VER="1.8.4"
INSTALL_DIR="/opt/mediamtx"
REPO_ROOT="$(cd "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
CONF_SRC="$REPO_ROOT/nano/mediamtx/mediamtx.yml"

case "$(uname -m)" in
    aarch64) TARBALL_ARCH="linux_arm64v8" ;;
    armv7l)  TARBALL_ARCH="linux_armv7"   ;;
    x86_64)  TARBALL_ARCH="linux_amd64"   ;;
    *) echo "unsupported arch: $(uname -m)"; exit 1 ;;
esac

URL="https://github.com/bluenviron/mediamtx/releases/download/v${VER}/mediamtx_v${VER}_${TARBALL_ARCH}.tar.gz"

echo "== fetch MediaMTX ${VER} (${TARBALL_ARCH}) =="
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$URL" -o "$TMP/mediamtx.tar.gz"

echo "== install to ${INSTALL_DIR} =="
sudo mkdir -p "$INSTALL_DIR"
sudo tar -C "$INSTALL_DIR" -xzf "$TMP/mediamtx.tar.gz" mediamtx
sudo install -m 644 "$CONF_SRC" "$INSTALL_DIR/mediamtx.yml"
sudo chmod 755 "$INSTALL_DIR/mediamtx"

echo "== installed binary: =="
"$INSTALL_DIR/mediamtx" --version || true

cat <<EOF

Next:
  make -C deploy install-nano-units     # copies mediamtx.service into user systemd
  systemctl --user enable --now mediamtx
  journalctl --user-unit=mediamtx -f    # camera-pipeline publishes into it

Then open in a browser on this LAN:
  http://$(hostname -s).local:8889/live    (MediaMTX built-in WHEP player)

Stop with:
  systemctl --user stop mediamtx
EOF

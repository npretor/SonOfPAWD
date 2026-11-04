# SonOfPAWD deployment helpers. Everything runs on the Jetson Nano.
# Run on the Nano from the repo root:  make -C deploy <target>

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

NANO_SERVICES := mediamtx recorder hub ui-backend inference \
                 sensor-wakeup ir-led motion-ctrl fire-control
# camera-pipeline is a *system* unit (see nano/systemd/system/); managed with sudo.
CAMERA_UNIT   := camera-pipeline
CAMERA_HELPER := /usr/local/lib/turret/camera-recover.sh

NANO_USER_UNIT_DIR := $(HOME)/.config/systemd/user

UI_DIR := $(REPO_ROOT)/nano/src/ui/frontend

.PHONY: help \
        install-nano-units enable-nano-units restart-nano stop-nano start-nano logs-nano status-nano \
        install-camera-unit restart-camera stop-camera logs-camera status-camera \
        test ui-install ui-build ui-dev

help:
	@echo "User units [$(NANO_SERVICES)]:"
	@echo "  install-nano-units copy systemd unit files into ~/.config/systemd/user and reload"
	@echo "  enable-nano-units  enable them to start on boot"
	@echo "  restart-nano / stop-nano / start-nano / logs-nano / status-nano"
	@echo ""
	@echo "camera-pipeline (system unit; needs sudo):"
	@echo "  install-camera-unit  install unit + root-owned recovery helper, enable"
	@echo "  restart-camera / stop-camera / logs-camera / status-camera"
	@echo ""
	@echo "  test               run the hardware-free unit tests"
	@echo "  ui-install / ui-build / ui-dev"

# --- Nano targets (run locally on the Nano; no SSH) ---

install-nano-units:
	mkdir -p $(NANO_USER_UNIT_DIR)
	cp $(REPO_ROOT)/nano/systemd/*.service $(NANO_USER_UNIT_DIR)/
	systemctl --user daemon-reload

enable-nano-units:
	systemctl --user enable $(NANO_SERVICES)
	loginctl enable-linger $(USER) || true

restart-nano:
	systemctl --user restart $(NANO_SERVICES)

stop-nano:
	systemctl --user stop $(NANO_SERVICES)

start-nano:
	systemctl --user start $(NANO_SERVICES)

logs-nano:
	journalctl -f $(foreach s,$(NANO_SERVICES),--user-unit=$(s))

status-nano:
	@for s in $(NANO_SERVICES); do systemctl --user status $$s --no-pager -n 5 || true; echo; done

# --- Nano camera-pipeline (system unit, run on the Nano) ---
# The recovery helper runs as root (ExecStopPost=+), so it is installed
# root-owned outside the repo; re-run this target after editing it.

install-camera-unit:
	sudo install -d -m 755 -o root -g root /usr/local/lib/turret
	sudo install -m 755 -o root -g root $(REPO_ROOT)/nano/scripts/camera_recover.sh $(CAMERA_HELPER)
	sed -e 's|@USER@|$(USER)|g' -e 's|@REPO@|$(REPO_ROOT)|g' \
	    $(REPO_ROOT)/nano/systemd/system/$(CAMERA_UNIT).service \
	    | sudo tee /etc/systemd/system/$(CAMERA_UNIT).service >/dev/null
	sudo systemctl daemon-reload
	sudo systemctl enable $(CAMERA_UNIT)
	@echo "Installed. Start with: sudo systemctl start $(CAMERA_UNIT)"

restart-camera:
	sudo systemctl restart $(CAMERA_UNIT)

stop-camera:
	sudo systemctl stop $(CAMERA_UNIT)

logs-camera:
	journalctl -f -u $(CAMERA_UNIT)

status-camera:
	systemctl status $(CAMERA_UNIT) --no-pager -n 20 || true

test:
	cd $(REPO_ROOT) && python3 -m unittest discover -s nano/tests -t .

# --- UI targets (run on the Nano or a dev box with Node 18+) ---

ui-install:
	cd $(UI_DIR) && npm install

ui-build:
	cd $(UI_DIR) && npm run build

ui-dev:
	cd $(UI_DIR) && npm run dev

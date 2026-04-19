# SonOfPAWD deployment helpers.
# Run from the repo root:  make -C deploy deploy-pi

REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
PI_HOST   ?= testcam-64bit.local
PI_USER   ?= nathan
PI_DEST   ?= /home/$(PI_USER)/SonOfPAWD
SSH_OPTS  ?= -o StrictHostKeyChecking=accept-new

PI_SERVICES := camera-pipeline sensor-wakeup ir-led

RSYNC_FLAGS := -avz --delete --exclude-from=$(REPO_ROOT)/deploy/rsync-exclude.txt

.PHONY: help deploy-pi install-pi-units enable-pi-units restart-pi stop-pi start-pi logs-pi status-pi ssh-pi

help:
	@echo "Targets:"
	@echo "  deploy-pi          rsync pi/ and common/ to the Pi, then restart services"
	@echo "  install-pi-units   copy systemd unit files to the Pi and daemon-reload"
	@echo "  enable-pi-units    enable all Pi services to start on boot"
	@echo "  restart-pi         systemctl --user restart \$(PI_SERVICES) on Pi"
	@echo "  stop-pi            stop Pi services"
	@echo "  start-pi           start Pi services"
	@echo "  logs-pi            journalctl -f for all Pi services"
	@echo "  status-pi          systemctl --user status for each Pi service"
	@echo "  ssh-pi             interactive SSH to the Pi"
	@echo ""
	@echo "Override with e.g.  make deploy-pi PI_HOST=192.168.1.42"

deploy-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) "mkdir -p $(PI_DEST)/pi $(PI_DEST)/common"
	rsync $(RSYNC_FLAGS) $(REPO_ROOT)/pi/     $(PI_USER)@$(PI_HOST):$(PI_DEST)/pi/
	rsync $(RSYNC_FLAGS) $(REPO_ROOT)/common/ $(PI_USER)@$(PI_HOST):$(PI_DEST)/common/
	@echo "Deployed. Use 'make restart-pi' to apply code changes."

install-pi-units:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) "mkdir -p ~/.config/systemd/user"
	scp $(SSH_OPTS) $(REPO_ROOT)/pi/systemd/*.service \
	    $(PI_USER)@$(PI_HOST):~/.config/systemd/user/
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) "systemctl --user daemon-reload"

enable-pi-units:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) \
	    "systemctl --user enable $(PI_SERVICES) && loginctl enable-linger $(PI_USER) || true"

restart-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) "systemctl --user restart $(PI_SERVICES)"

stop-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) "systemctl --user stop $(PI_SERVICES)"

start-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) "systemctl --user start $(PI_SERVICES)"

logs-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) \
	    "journalctl --user -f $(foreach s,$(PI_SERVICES),-u $(s))"

status-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST) \
	    "for s in $(PI_SERVICES); do systemctl --user status \$$s --no-pager -n 5 || true; echo; done"

ssh-pi:
	ssh $(SSH_OPTS) $(PI_USER)@$(PI_HOST)

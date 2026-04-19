  1. Nano: sudo apt install mosquitto mosquitto-clients python3-paho-mqtt and sudo systemctl enable --now mosquitto.
  2. Pi: ensure SSH key auth from Nano, then make -C deploy deploy-pi (copies code), then SSH in and run
  ~/SonOfPAWD/pi/scripts/host_prep.sh.
  3. Edit common/config/hosts.py — set PIR_PIN and IR_LED_PIN to your actual wiring.
  4. Smoke-test MQTT (plan §4): mosquitto_sub -h localhost -t 'turret/#' -v on the Nano, then on the Pi run python3 -m
  common.messaging.examples.pub_test. You should see five turret/test/ping messages.
  5. Install + enable Pi units: make -C deploy install-pi-units enable-pi-units, then make -C deploy deploy-pi once more to start them.
  6. Verify the camera service (plan step 2 done-when): VLC → rtsp://raspberrypi.local:8554/live after a Pi reboot, no manual commands.

  When all three Pi services are green, say the word and I'll start on nano/src/recorder.


# Setup ssh make deploy
Set up the Nano so it can ssh nathan@testcam-64bit.local without typing a password. The Makefile uses ssh, scp, and rsync under the hood — if every invocation prompts for a password, make deploy-pi becomes painful and you'll start cutting corners.


### One-time setup, from the Nano:   

### 1. Generate a key (skip if ~/.ssh/id_ed25519 already exists)                                                                         
```ssh-keygen -t ed25519 -C "nano-to-pi" ``` 

### 2. Copy the public key to the Pi (will prompt for Pi password once)                                                                  
```ssh-copy-id nathan@testcam-64bit.local```     

### 3. Verify — this should NOT prompt                                                                                                   
```ssh nathan@testcam-64bit.local 'hostname'```
                                                                                    
After step 3 succeeds, every make target hits the Pi instantly with no prompts. That's the only prerequisite.      
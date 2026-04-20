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


## Video recording setup 
What was added                                                                                                                         
                                                                                                                                         
  - nano/src/recorder/main.py — Recorder class. RTSP → rtph264depay → h264parse → valve(gate) → splitmuxsink(mux). Owns the              
  trailing-window timer; rearm cancels pending close. Cached arm state survives reconnect. Bus watch handles EOS/ERROR with 2s backoff.  
  Filenames are clip_YYYYMMDD_HHMMSS.mp4. MQTT events fire on actual file open/close, not arm transitions.                               
  - nano/systemd/recorder.service — user unit, After=mosquitto.service, Restart=on-failure. WorkingDirectory=%h/github/SonOfPAWD.        
  - nano/scripts/host_prep.sh — installs mosquitto + GStreamer + paho, enables mosquitto/chrony, lingers user, creates and chowns        
  /var/lib/turret/clips, verifies all required GStreamer elements exist, smoke-tests mosquitto.                                          
  - deploy/Makefile — added install-nano-units, enable-nano-units, restart-nano, stop-nano, start-nano, logs-nano, status-nano. Run      
  locally on the Nano.   

# Testpath 

### 1. one-time prep     
```bash nano/scripts/host_prep.sh```

### 2. install + enable the unit               
```make -C deploy install-nano-units enable-nano-units```    
        
### 3. start it 
```     
make -C deploy start-nano     
make -C deploy logs-nano    # watch for "pipeline PLAYING (rtsp=...)"
```

### 4. arm and look for a file                                                                                                           
mosquitto_pub -h localhost -t turret/recorder/arm -m '{"armed":true}'          

```ls -lh /var/lib/turret/clips/ ```           
                                                                                                                     
### 5. wait a few seconds, disarm with short trailing                                                                                    
```mosquitto_pub -h localhost -t turret/recorder/arm -m '{"armed":false,"trailing_s":3}' ``` 

# ~3s later: clip_closed event in the journal, file finalized 
         
### 6. play it       

```vlc /var/lib/turret/clips/clip_*.mp4```    

If the clip plays cleanly, recorder is done and the only thing standing between you and a working motion-triggered DVR is the hub. Say the word and I'll start that.     


Modes for the core service 
- Auto-aim 
    - wakes upon detection of PIR event sand saves video, 
    - classifies and tracks one type of object if seen  
- Auto-sentry 
    - wakes upon detection of PIR event and saves video, 
    - classifies and tracks one type of object if seen
    - also auto-fires when centered in crosshairs 
- Monitor 
    - wakes after PIR event and saves video 
- trailcam
    - image save after PIR event, no video 
- Idle
    - Does nothing, waits for other modes to be activated
    - (impliment later) low power mode 


Core specs 
- Responds to mode adjustments and sets logic and service orchestration accordingly. Mode adjustments are set from the local flask web interface(to be implimented after the core service) 
- Logs service status and health. This should also be saved in a way the control interface website can access it when implimented later
- Monitors remaining disk space and makes it available for the control website
- Monitors the nano's temperature, CPU usage, and RAM usage and makes it available for the control website

The control website will be locally run from the jetson nano 

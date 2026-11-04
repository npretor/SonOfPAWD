1. Mode transport. I want this to be a flask backend as a separate service that sends mqtt mode info to and from the hub.
2. Mode persistence. After a reboot, default to idle, and add a shim function for alerting the user that will be implimented later
3. Grabbing a keyframe from teh RTSP stream is perfect
4. The target classs will be selectable types of animals(cat, racoon, rat, deer). It's for keeping animals out of a garden.
5. The current plan is to have a MOSFET activate a solenoid for a water cannon for the actual firing. This will be activated by a rpi gpio pin. However, i also want to be able to cut power to the whole circuit via a rpi gpio pin as well. In addition there will be a physical switch that cuts power to the solenoid
6. The pan tilt hardware is two servos currently. I need to flesh out the control plan more later, but the plan is for a microcontroller that receives motion commands. The inference command routes through the hub. I also want to be able to arm/disarm the pan-tilt hardware. 
7. TensorRT most likely, but i need to find a model that can differentiate between types of animals. Racoons are rare in most DNN models
8. Telemetry delivery should be publish on interval, flask subscribes
9. Manual deletion. I will save clips to an external storage device 
10. Yes, enable username and password
11. Idle means motion events are logged, but nothing else happens. Minimal energy usage in this state. 
12. IR should be triggered whenever a clip is being recorded, but also toggleable on from the frontend, and enabled when the trigger/motion system is activated. 
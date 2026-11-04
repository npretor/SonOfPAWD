Plan how to attach a motion system. 

This is the current motion configuration: 
Two servos, each has the same range of motion. 
The camera, water cannon, and IR light will be mounted on top of the vertical motion servo, which is on top of the horizontal motion servo. The horizontal motion servo is fixed to a base. 
The servos are controlled by an Arduino which receives serial commands via serial over USB. 
The controller by default starts the servos in the zero angle (1500ms pulses)
The protocol the controller receives is <x_servo_microseconds, y_servo_microseconds>, for example <1500, 1200>, or to zero: <1500, 1500>

The power supply to the servos has an enable, controlled by a GPIO pin that is yet to be determined. 

Recommend a GPIO pin to enable to motion, and one for the trigger solenoid for the water switch for the cannon. They should be active high. 

Brainstorm ideas for how to control the motion system. This should receive motion values or commands from the hub that are input by the user, or by the auto-sentry targeting algorithm. 

Does it matter if it's connected to the Jetson or Raspberry Pi? 

# 2. In one terminal, watch the aim topic                                                                                              
mosquitto_sub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)" -t 'turret/motion_ctrl/aim' -t 'turret/motion_ctrl/state' -v                                                                         
                                                                                                                                        
# 3. In another, set up the auto-aim mode + class                                                                                      
mosquitto_pub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)" -t turret/mode/set -m '{"mode":"auto-aim"}'                                                                                          
mosquitto_pub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)" -t turret/hub/config -r -m '{"target_class":"raccoon"}'                                                                              
                                                                                                                                        
# 4. Fire the synthetic detection                                                                                                      
mosquitto_pub -h localhost -u turret -P "$(cat /etc/turret/mqtt.password)" -t turret/inference/detection -m '{"class":"raccoon","score":0.9,"bbox":[0.2,0.3,0.4,0.5]}'

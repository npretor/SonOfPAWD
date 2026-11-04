  What it does:                                                                                                                          
  - nano/src/hub/lockon.py — pure state machine, easy to unit-test (just did).                                                           
  - Hub feeds gate from _handle_detection only when mode is auto-sentry; on lock, publishes turret/fire/request only if fire/armed AND   
  fire/enable retained are both true.                                                                                                    
  - Aim-settled check uses motion_ctrl/state deltas; permissive when the stream is stale so synthetic tests still work.                  
  - Mode side-effects flip fire/enable on auto-sentry crossings (auto-aim no longer enables fire).                     
  - 100 ms tick handles cooldown expiry and staleness; hub_state republishes only when the lockon snapshot changes.                      
  - UI badge in Dashboard.tsx: ACQUIRING n/N (yellow) → LOCKED (red, pulses) → cooldown 4.9s (muted).                                    
                                                                                                                                         
  Test path (with motion-ctrl on the Pi up):                                                                                             
  mosquitto_pub -t turret/mode/set -m '{"mode":"auto-sentry"}'                                                                           
  mosquitto_pub -t turret/hub/config -r -m '{"target_class":"raccoon"}'                                                                  
  # Click ARM in the UI (sets fire/armed retained true)                                                                                  
                                                                                                                                         
  # 4 quick consistent detections → expect ONE fire/request                                                                              
  for i in 1 2 3 4; do                                                                                                                   
    mosquitto_pub -t turret/inference/detection \                                                                                        
      -m '{"detections":[{"class":"raccoon","score":0.9,"bbox":[0.45,0.45,0.55,0.55]}]}'                                                 
    sleep 0.1                                                                                                                            
  done                                                                                                                                   
  mosquitto_sub -t turret/fire/request -C 1   # should print one message                                                                 
                                                                                                                                         
  Tunables (N, WINDOW_S, SCORE_FIRE_MIN, CENTROID_TOL, COOLDOWN_S, SETTLED_TOL_US) live as class attrs on LockonGate. Easy to expose via 
  hub/config later if you want the UI to drive them. 

### Turret interface 
The turret interface should be running at all times. It should be the main interface for the user  
The video however, only activates when toggled on 


Several tasks
1. Displays service statuses to the user, and shows logs 
2. Show turret controls 
3. Show video 
4. Set turret status 
5. Surface settings that will be added later



## User interface 
### Primary landing page (/)
Sidebar 1/4 of the page left side 
- service health list in one section (red=down, green=up) 
- Live updating turret angle axes and limits (fake info for now, later it will be streamed) 
- Disk usage for video saving, RAM usage, power usage. Updates every second or so 


Main page has a video streaming taking up 3/4 right side. Toggle starts and stops displaying video

Mode selection dropdwn, mode changes on selection
Video overlays: 
- Mode selection 
- Framerate 
- Resolution 
- RSSI if possible


Add a red fire button for manual firing
Add a toggle to show streaming status logs on the bottom of the page
Add three options for turret motor control to toggle between (keyboard arrow keys  with up/down/left/right, ipad/iphone tilt detection mapping to turret axes, and a joystick style on-screen touch control on the right side of the page). Only one can be active at a time, default to joystick controls. 

### Settings page (/settings)
A list of settings that can be edited. Settings saved on the backend. 
Logs can be accessed here
Things like motion control speed set here also, but added later. Add a placeholder 
Other: Motion detection timeout, some other granular settings 
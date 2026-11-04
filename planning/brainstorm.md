# Brainstorm

### The Specs
Current Hardware: 
- Jetson Nano
- IMX477 Camera
- PIR sensor(s) connected to GPIO pins. Specifically the HC-SR501, 3.3v logic, goes high when motion is detected.
- GPIO to enable IR LEDs for the camera 


### I want to start a robotics project with several major parts 
1. A continuously running wake-up service for sensors. Right now this will be a PIR sensor, later on a higher-resolution thermal sensor 
2. (TO BE BUILT LATER) A website showing the status of the turret. Video can be toggled on and off. 
3. A video streaming service. This can be woken up by the sensor wake-up service, or the website.
4. A video saving service. When wake-ups occur, video is saved for a given duration after the last PIR detection event. 
5. (TO BE BUILT LATER) A realtime video classification service(or an image frame classification service if there is a difference). Ideally utilizing TensorRT.  
6. (TO BE BUILT LATER). Motion control for at least two axes of motion and several GPIO pins. 
7. A central hub that controls the logic and operations. This should monitor the status of the services, and tell them what to do. 

I would like all of these parts to be running as services independently. One messaging option i've considered is ZeroMQ. 
I'm unsure what to use for images, some options are: Imagezmq, gstreamer. 

## Give some options for 
Brainstorm what architecture works for comunicaion between services 
Brainstorm what is best for low latency video streaming. 
Prefer hardware acceleration where possible, especially for video and inference processing. 
I would like to use python to control all of this if possible


Save of this as plan1.md 


Build a spec for a realtime video classification service(or an image frame classification service if there is a difference), ideally utilizing TensorRT. 
I'm aiming for speed over other factors for now, it will run on the Jetson. 
I am planning to track one or more racoons, cats, rats, etc in the garden(as already implimented), but only focus on one at the time for the turret's purposes. If there are several of one class in the image, focus on the one with the highest classification accuracy. 

Do models using TensorRT have to be a certain size, with given specs? Prefer some of the top image classification models for cats for now(Like Yolo, etc), but later on i need to find a model that can handle other mammals like racoons and maybe even gophers in the future. What are my options for near-realtime classification. 



Currently, the project contains commented out VM, subnet and NSG that can be set as a frontend reverse
proxy or a load balancer and potentially can be a better option because of the better customization.


No firewall rules configured because of the small project size.

PostgreSQL flexible server is used as a default DB

Azure Load Balancer is used. No TLS termination on this step, only traffic redirection.
TLS termination is performed on the background apps and later will be moved to the Application Gateway.

All inbound internet traffic passes through Azure BL. Backend and DB are not exposed to the public internet
and isolated inside the private subnet with inbound and outbound security rulesets. Communication LB-APP, 
as well as APP-DB are limited to only one port in order to reduce the potential attack surface.

Current hosting view of the project is as follows:
Azure LB → Node → Ingress pod → App pod → DB

Current traffic chain of the project is as follows:
Client → Azure LB → Ingress controller (NGINX) → Service → Pod → DB

Resource Visualization diagram:
![WestUS_2.jpg](../../../Downloads/WestUS_2.jpg)
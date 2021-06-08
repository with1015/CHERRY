### CHERRY: Dynamic Cache Management for fast-efficient start up in PMEM based FaaS system  
#### UNIST AOS 2021 Project, Sanghyun Eum & Hyunjoon Jeong  
---
### 1. Requirments  
* Python >= 3.6  
* Docker >= 1.18.0  
* Python Docker engine API >= 5.0.0
 > Docker engine API can be installed following command in Linux  
```
pip3 install docker==5.0.0
```  
---
### 2. Daemon process mode usage  
```
python3 pm_monitor.py  
python3 docker_monitor.py  
```
---
### 3. Embedded mode usage
```
# In your code  
From CHERRY.docker_monitor import DockerMonitor  
From CHERRY.pm_monitor import PMemMonitor  

DM = DockerMonitor()  
PM = PMemMonitor()  
DM.start()  
PM.start()  
```
---

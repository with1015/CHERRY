#!/bin/bash

NVME_PATH="/mnt/nvme/docker_images/overlay2"
PMEM_PATH="/mnt/pm/docker_images"


TARGETS=($(ls $PMEM_PATH | awk '{print $1}'))


for i in ${TARGETS[@]}
do
	cd $NVME_PATH
	rm $i
	cd $PMEM_PATH
	mv $i $NVME_PATH/
done

# Remove quit containers in docker ps
docker rm $(docker ps -a -q)

cd /home/sheum/hello-bench/CHERRY

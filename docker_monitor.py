import os
import docker
import subprocess
import time
import operator
import copy

from threading import Thread, Lock
from CHERRY.pm_monitor import PMemMonitor
#from pm_monitor import PMemMonitor

def get_dir_size(path="."):
	cmd = "du " + path + " | awk '{print $1}'"
	total = subprocess.check_output([cmd], shell=True, encoding="utf-8").split("\n")[-2]
	return total


def convert_hash_to_real(target_sha):
	cmd = "grep -r " + target_sha + " " + "/image/overlay2/layerdb/sha256/"


class DockerImage():
	def __init__(self, target=None, docker_path="None"):
		self.img = target
		self.layers = []
		self.dummy_hash = None
		self.layer_path = {}
		self.layer_size = {}
		self.docker_path = docker_path

	def set_layers(self):
		dummy_layers = self.img.attrs['RootFS']['Layers']
		self.dummy_hash = dummy_layers
		for layer_hash in dummy_layers:
			cmd = "grep -r " + layer_hash + " " + self.docker_path + "/image/overlay2/layerdb/sha256/"
			output = subprocess.check_output([cmd], shell=True, encoding='utf-8').split('\n')
			for link in output:
				if "diff" in link:
					target_cmd = "cat " + link.split('/diff')[0] + "/cache-id"
					output = subprocess.check_output([target_cmd], shell=True, encoding='utf-8')
					self.layers.append(output)
					self.layer_path[output] = self.docker_path + "/overlay2/" + output
					self.layer_size[output] = get_dir_size(path=self.layer_path[output])


class DockerMonitor(Thread):

	def __init__(self, name="None", docker_path="/mnt/nvme/docker_images", limit=1000000):
		super().__init__()
		self.init_lock = Lock()
		self.init_lock.acquire()
		self.name = name
		self.client = docker.from_env()
		self.docker_path = docker_path
		self.image_list = {}
		self.container_list = []
		self.layer_cnt = {}
		self.cached_list = {}
		self.PMEM = PMemMonitor(name="pm", limit=limit)
		self.shutdown = True
		self.lock = Lock()


	def run(self):
		self.PMEM.start()
		print("Initializing Docker status ...")
		self.set_image_list()
		print("Checked Docker status!")
		print("Initial image caching start...")
		self.lock.acquire()
		self.cache_image_to_pmem()
		self.lock.release()
		print("Done...!")
		self.init_lock.release()

		while self.shutdown == True:
			self.lock.acquire()
			ret, new_comes = self.check_new_containers()
			if ret == True:
				self.rearrange_priority(new_comes)
				self.cache_image_to_pmem()
			self.lock.release()
			time.sleep(1)


	def thread_off(self):
		self.shutdown = False
		self.PMEM.shutdown = False
		self.PMEM.join()
		print("Docker monitoring off...")


	def set_image_list(self):
		image_list = self.client.images.list()
		for img in image_list:
			target = DockerImage(target=img, docker_path=self.docker_path)
			key = target.img.id
			if key not in self.image_list.keys():
				target.set_layers()
				self.image_list[key] = target
				for layer in target.layers:
					if layer in self.layer_cnt.keys():
						self.layer_cnt[layer] += 1
					else:
						self.layer_cnt[layer] = 1


	def get_layer_path(self, target):
		for img in self.image_list.values():
			if target in img.layer_path.keys():
				path = img.layer_path[target]
				return path


	def get_layer_size(self, target):
		for img in self.image_list.values():
			if target in img.layer_path.keys():
				size = img.layer_size[target]
				return size



	def move_nvme_to_pmem(self, layer, pm_path):
		cur_path = self.get_layer_path(layer)
		symbolic = pm_path + "/docker_images/" + layer
		mv_cmd = "mv " + cur_path + " " + pm_path + "/docker_images"
		ln_cmd = "ln -s " + symbolic + " " + cur_path
		self.PMEM.lock.acquire()
		subprocess.run([mv_cmd + ";" + ln_cmd], shell=True)
		self.PMEM.lock.release()
	

	def move_pmem_to_nvme(self, layer, pm_path):
		original_path = self.get_layer_path(layer)
		rm_cmd = "rm " + original_path
		mv_cmd = "mv " + pm_path + "/docker_images/" + layer + " " + original_path
		self.PMEM.lock.acquire()
		subprocess.run([rm_cmd + ";" + mv_cmd], shell=True)
		self.PMEM.lock.release()


	def cache_image_to_pmem(self):
		if self.PMEM.status == False:
			return
		priority_layers = sorted(self.layer_cnt.items(), key=operator.itemgetter(1), reverse=True)
		print("Caching images...")

		for layer in priority_layers:
			layer_name = layer[0]
			layer_priority = layer[1]

			if layer_priority > 1 and layer_name not in self.cached_list.keys():
				layer_size = self.get_layer_size(layer_name)

				if self.PMEM.check_cache_available(layer_size):
					path = self.get_layer_path(layer_name)
					self.cached_list[layer_name] = path
					self.move_nvme_to_pmem(layer=layer_name, pm_path="/mnt/pm")
					#print("[Cached] ", layer_name, "size:", layer_size, "KB")
				else:
					self.evict_from_pmem(layer_name)
					path = self.get_layer_path(layer_name)
					self.cached_list[layer_name] = path
					self.move_nvme_to_pmem(layer=layer_name, pm_path="/mnt/pm")
					#print("[Cached] ", layer_name, "size:", layer_size, "KB")


	def evict_from_pmem(self, target_layer):
		dump_list = list(self.cached_list.keys())
		victim_list = copy.deepcopy(dump_list)
		target_layer_size = self.get_layer_size(target_layer)
		target_priority = self.layer_cnt[target_layer]

		for victim_name in victim_list:
			victim_priority = self.layer_cnt[victim_name]
			if victim_priority < target_priority:
				continue
			self.move_pmem_to_nvme(victim_name, self.PMEM.pm_path)
			victim_size = self.get_layer_size(victim_name)
			#print("[Evicted] ", victim_name, "size:", victim_size, "KB")
			#print("sharing these images:", self.get_image_from_layer(victim_name))
			del self.cached_list[victim_name]
			if self.PMEM.check_cache_available(target_layer_size):
				return


	def get_image_from_layer(self, layer_code):
		result = []
		for img in self.image_list.items():
			layers = img[1].layers
			for layer in layers:
				if layer_code == layer:
					result.append(img[1].img.tags)
		return result


	def check_new_containers(self):
		current = self.client.containers.list(all=True)
		current_num = len(current)
		prev_num = len(self.container_list)
		if current_num != prev_num:
			new_come = [x for x in current if x not in self.container_list]
			self.container_list = current
			return True, new_come
		return False, None


	def rearrange_priority(self, new_comes):
		for target in new_comes:
			target_id = target.image.id
			for key in self.image_list.keys():
				if key == target_id:
					img = self.image_list[key]
					for layer in img.layers:
						self.layer_cnt[layer] += 1
					break


if __name__ == "__main__":
	test = DockerMonitor()
	test.start()
	#test.thread_off()
	test.join()

import os
import docker
import subprocess
import time
import operator
import copy

from threading import Thread, Lock
from pm_monitor import PMemMonitor


def get_dir_size(path="."):
	cmd = "du " + path + " | awk '{print $1}'"
	total = subprocess.check_output([cmd], shell=True, encoding="utf-8").split("\n")[-2]
	return total


class DockerImage():
	def __init__(self, target=None, docker_path="None"):
		self.img = target
		self.layers = []
		self.layer_path = {}
		self.layer_size = {}
		self.docker_path = docker_path

	def set_layers(self):
		dummy_layers = self.img.attrs['RootFS']['Layers']
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
		self.name = name
		self.client = docker.from_env()
		self.docker_path = docker_path
		self.image_list = {}
		self.layer_cnt = {}
		self.cached_list = {}
		self.PMEM = PMemMonitor(name="pm", limit=limit)
		self.new_come = True
		self.shutdown = True
		self.lock = Lock()


	def run(self):
		self.PMEM.start()
		print("Initializing Docker status ...")
		self.set_image_list()
		print("Checked Docker status!")

		while self.shutdown == True:
			self.set_image_list()
			if self.new_come == True:
				self.lock.acquire()
				self.cache_to_pmem()
				self.lock.release()
			time.sleep(1)


	def thread_off(self):
		self.shutdown = False
		self.PMEM.shutdown = False
		self.PMEM.join()


	def set_image_list(self):
		image_list = self.client.images.list()
		if self.new_come == False:
			return
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
		subprocess.run([mv_cmd + ";" + ln_cmd], shell=True)
	

	def move_pmem_to_nvme(self, layer, pm_path):
		original_path = self.get_layer_path(layer)
		rm_cmd = "rm " + original_path
		mv_cmd = "mv " + pm_path + "/docker_images/" + layer + " " + original_path
		subprocess.run([rm_cmd + ";" + mv_cmd], shell=True)


	def cache_to_pmem(self):
		if self.PMEM.status == False:
			return
		priority_layers = sorted(self.layer_cnt.items(), key=operator.itemgetter(1), reverse=True)
		print("Caching...")

		for layer in priority_layers:
			layer_name = layer[0]
			layer_priority = layer[1]

			if layer_priority > 1 and layer_name not in self.cached_list.keys():
				layer_size = self.get_layer_size(layer_name)

				if self.PMEM.check_cache_available(layer_size):
					path = self.get_layer_path(layer_name)
					self.cached_list[layer_name] = path
					self.move_nvme_to_pmem(layer=layer_name, pm_path="/mnt/pm")
					print("[Cached] ", layer_name, "size:", layer_size, "KB")
					print("Sharing these images:", self.get_image_from_layer(layer_name))
				else:
					self.evict_from_pmem(layer_name)
					path = self.get_layer_path(layer_name)
					self.cached_list[layer_name] = path
					self.move_nvme_to_pmem(layer=layer_name, pm_path="/mnt/pm")
					print("[Cached] ", layer_name, "size:", layer_size, "KB")
					print("sharing these images:", self.get_image_from_layer(layer_name))

		self.new_come = False


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
			print("[Evicted] ", victim_name, "size:", victim_size, "KB")
			print("sharing these images:", self.get_image_from_layer(victim_name))
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


if __name__ == "__main__":
	test = DockerMonitor()
	test.start()
	#test.thread_off()
	test.join()

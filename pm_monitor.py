from threading import Thread, Lock
import time
import subprocess


class PMemMonitor(Thread):
	def __init__(self, name="PMemMonitor", pm_path="/mnt/pm", limit="-1", verbose=False):
		super().__init__()
		self.name = name
		self.pm_path = pm_path
		self.docker_path = pm_path + "/docker_images"
		self.limit = int(limit)
		self.status = True
		self.shutdown = True
		self.verbose = verbose
		self.lock = Lock()

		self.total 		  = 0
		self.usage		  = 0
		self.available 	  = 0
		self.docker_usage = 0


	def run(self):
		print("PMEM monitoring start.")
		pm_cmd = "df | grep pmem0"
		docker_cmd = "du " + self.docker_path + " -d 1 | awk '{print $1}'"

		while self.shutdown == True:
			self.lock.acquire()
			output = subprocess.check_output([pm_cmd], shell=True, encoding='utf-8').split(" ")
			output = list(filter(lambda x: x != "", output))
			self.total = int(output[1])
			self.usage = int(output[2])
			self.available = int(output[3])

			output = subprocess.check_output([docker_cmd], shell=True, encoding='utf-8').split("\n")
			output = list(map(int, filter(lambda x: x != "", output)))
			self.docker_usage = int(sum(output))
			self.lock.release()

			if self.verbose == True:
				print("=======================================================")
				print("Total capacity\t", self.total, "KB")
				print("Current usage\t", self.usage, "KB")
				print("Docker usage\t", self.docker_usage, "KB")
				print("Available\t", self.available, "KB")
			
			time.sleep(1)
	

	def thread_off(self):
		self.shutdown = False


	def check_cache_available(self, layer_size):
		self.lock.acquire()
		if self.docker_usage + int(layer_size) <= self.limit:
			self.lock.release()
			return True
		self.lock.release()
		return False



if __name__ == "__main__":
	test = PMemMonitor(name="test",
						pm_path="/mnt/pm",
						verbose=True)
	test.start()
	test.join()

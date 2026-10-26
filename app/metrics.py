"""Arka planda sistem metriklerini toplayan örnekleyici."""
import os
import platform
import socket
import threading
import time
from collections import deque

import psutil

try:
    import pynvml
except ImportError:  # NVIDIA kartı / kütüphanesi yoksa GPU bölümü boş kalır
    pynvml = None

winproc = None
if os.name == "nt":
    try:
        import winproc
    except OSError:
        winproc = None


def _cpu_model():
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or "CPU"


class Sampler:
    def __init__(self, interval=1.0, proc_interval=2.0, history=180):
        self.interval = interval
        self.proc_interval = proc_interval
        self.lock = threading.Lock()
        self.snapshot = {}
        self.processes = []
        self.history = deque(maxlen=history)
        self._io_prev = {}
        self._ident_cache = {}
        self._gpu_handles = []
        self.system = self._system_info()
        self._init_gpu()

    # ---------------------------------------------------------------- setup
    def _system_info(self):
        return {
            "hostname": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "cpu_model": _cpu_model(),
            "cores_physical": psutil.cpu_count(logical=False),
            "cores_logical": psutil.cpu_count(),
            "ram_total": psutil.virtual_memory().total,
            "boot_time": psutil.boot_time(),
        }

    def _init_gpu(self):
        if not pynvml:
            return
        try:
            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                self._gpu_handles.append(pynvml.nvmlDeviceGetHandleByIndex(i))
        except pynvml.NVMLError:
            self._gpu_handles = []

    def start(self):
        threading.Thread(target=self._metrics_loop, daemon=True).start()
        threading.Thread(target=self._procs_loop, daemon=True).start()

    # ---------------------------------------------------------------- gpu
    def _gpu(self):
        out = []
        for idx, h in enumerate(self._gpu_handles):
            g = {"index": idx}

            def safe(key, fn):
                try:
                    g[key] = fn()
                except pynvml.NVMLError:
                    g[key] = None

            safe("name", lambda: pynvml.nvmlDeviceGetName(h))
            safe("util", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
            safe("mem_util", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).memory)
            mem = None
            try:
                mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            except pynvml.NVMLError:
                pass
            g["mem_used"] = mem.used if mem else None
            g["mem_total"] = mem.total if mem else None
            safe("temp", lambda: pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
            safe("power", lambda: pynvml.nvmlDeviceGetPowerUsage(h) / 1000)
            safe("power_limit", lambda: pynvml.nvmlDeviceGetEnforcedPowerLimit(h) / 1000)
            safe("fan", lambda: pynvml.nvmlDeviceGetFanSpeed(h))
            safe("clock", lambda: pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS))
            safe("clock_max", lambda: pynvml.nvmlDeviceGetMaxClockInfo(h, pynvml.NVML_CLOCK_GRAPHICS))
            safe("mem_clock", lambda: pynvml.nvmlDeviceGetClockInfo(h, pynvml.NVML_CLOCK_MEM))
            out.append(g)
        return out

    # ---------------------------------------------------------------- loops
    def _disks(self):
        disks = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({
                "mount": part.mountpoint, "fstype": part.fstype,
                "total": u.total, "used": u.used, "free": u.free, "percent": u.percent,
            })
        return disks

    def _metrics_loop(self):
        psutil.cpu_percent(percpu=True)
        last_t = time.monotonic()
        last_disk = psutil.disk_io_counters()
        last_net = psutil.net_io_counters()
        while True:
            time.sleep(self.interval)
            now = time.monotonic()
            dt = max(now - last_t, 1e-3)
            last_t = now

            per_core = psutil.cpu_percent(percpu=True)
            cpu = sum(per_core) / len(per_core) if per_core else 0
            freq = psutil.cpu_freq()
            vm = psutil.virtual_memory()
            sw = psutil.swap_memory()

            disk = psutil.disk_io_counters()
            net = psutil.net_io_counters()
            disk_r = disk_w = net_up = net_down = 0
            if disk and last_disk:
                disk_r = (disk.read_bytes - last_disk.read_bytes) / dt
                disk_w = (disk.write_bytes - last_disk.write_bytes) / dt
            if net and last_net:
                net_up = (net.bytes_sent - last_net.bytes_sent) / dt
                net_down = (net.bytes_recv - last_net.bytes_recv) / dt
            last_disk, last_net = disk, net

            gpus = self._gpu()
            g0 = gpus[0] if gpus else {}
            gpu_mem_pct = None
            if g0.get("mem_total"):
                gpu_mem_pct = g0["mem_used"] / g0["mem_total"] * 100

            snap = {
                "time": time.time(),
                "cpu": {
                    "percent": cpu, "per_core": per_core,
                    "freq": freq.current if freq else None, "freq_max": freq.max if freq else None,
                },
                "ram": {"total": vm.total, "used": vm.used, "available": vm.available, "percent": vm.percent},
                "swap": {"total": sw.total, "used": sw.used, "percent": sw.percent},
                "gpus": gpus,
                "disks": self._disks(),
                "disk_io": {"read": disk_r, "write": disk_w},
                "net": {
                    "up": net_up, "down": net_down,
                    "sent_total": net.bytes_sent if net else 0, "recv_total": net.bytes_recv if net else 0,
                },
                "uptime": time.time() - self.system["boot_time"],
                "process_count": len(self.processes),
            }
            point = {
                "t": snap["time"], "cpu": cpu, "ram": vm.percent,
                "gpu": g0.get("util"), "vram": gpu_mem_pct, "gpu_temp": g0.get("temp"),
                "up": net_up, "down": net_down, "dr": disk_r, "dw": disk_w,
            }
            with self.lock:
                self.snapshot = snap
                self.history.append(point)

    def _procs_loop(self):
        if winproc:
            return self._procs_loop_windows()
        return self._procs_loop_psutil()

    def _proc_identity(self, pid, create):
        """Kullanıcı ve exe yolu süreç ömrü boyunca değişmez; (pid, başlangıç) başına bir kez sorulur."""
        key = (pid, create)
        ident = self._ident_cache.get(key)
        if ident is None:
            user = exe = ""
            try:
                p = psutil.Process(pid)
                try:
                    user = (p.username() or "").split("\\")[-1]
                except (psutil.AccessDenied, OSError):
                    pass
                try:
                    exe = p.exe() or ""
                except (psutil.AccessDenied, OSError):
                    pass
            except psutil.NoSuchProcess:
                pass
            ident = self._ident_cache[key] = (user, exe)
        return ident

    def _procs_loop_windows(self):
        ncpu = psutil.cpu_count() or 1
        prev = {}
        while True:
            now = time.monotonic()
            try:
                snap = winproc.snapshot()
            except OSError:
                time.sleep(self.proc_interval)
                continue
            rows, cur = [], {}
            for pid, name, threads, ws_private, _ws, cpu_t, create, rd, wr, suspended in snap:
                if pid == 0:  # System Idle Process
                    continue
                key = (pid, create)
                cur[key] = (now, cpu_t, rd, wr)
                cpu = io_r = io_w = 0.0
                old = prev.get(key)
                if old:
                    dt = max(now - old[0], 1e-3)
                    cpu = max(cpu_t - old[1], 0) / 1e7 / dt / ncpu * 100
                    io_r = max(rd - old[2], 0) / dt
                    io_w = max(wr - old[3], 0) / dt
                user, exe = self._proc_identity(pid, create)
                rows.append({
                    "pid": pid, "name": name or "?", "user": user, "cpu": round(cpu, 2),
                    "mem": ws_private, "threads": threads, "status": "stopped" if suspended else "running",
                    "started": create / 1e7 - 11644473600 if create else None,
                    "io_r": io_r, "io_w": io_w, "exe": exe,
                })
            prev = cur
            for key in [k for k in self._ident_cache if k not in cur]:
                del self._ident_cache[key]
            with self.lock:
                self.processes = rows
            time.sleep(max(self.proc_interval - (time.monotonic() - now), 0.2))

    def _procs_loop_psutil(self):
        ncpu = psutil.cpu_count() or 1
        attrs = ["name", "username", "memory_info", "num_threads", "status", "create_time", "exe"]
        while True:
            now = time.monotonic()
            rows, seen = [], set()
            for p in psutil.process_iter(attrs, ad_value=None):
                if p.pid == 0:  # System Idle Process
                    continue
                try:
                    cpu = p.cpu_percent(None) / ncpu
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    cpu = 0.0
                io_r = io_w = 0.0
                try:
                    io = p.io_counters()
                    prev = self._io_prev.get(p.pid)
                    if prev:
                        dt = max(now - prev[0], 1e-3)
                        io_r = max(io.read_bytes - prev[1], 0) / dt
                        io_w = max(io.write_bytes - prev[2], 0) / dt
                    self._io_prev[p.pid] = (now, io.read_bytes, io.write_bytes)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                seen.add(p.pid)
                info = p.info
                mem = info["memory_info"]
                user = info["username"] or ""
                rows.append({
                    "pid": p.pid,
                    "name": info["name"] or "?",
                    "user": user.split("\\")[-1],
                    "cpu": round(cpu, 2),
                    "mem": mem.rss if mem else 0,
                    "threads": info["num_threads"] or 0,
                    "status": info["status"] or "",
                    "started": info["create_time"],
                    "io_r": io_r,
                    "io_w": io_w,
                    "exe": info["exe"] or "",
                })
            for pid in list(self._io_prev):
                if pid not in seen:
                    del self._io_prev[pid]
            with self.lock:
                self.processes = rows
            time.sleep(max(self.proc_interval - (time.monotonic() - now), 0.2))

    # ---------------------------------------------------------------- readers
    def get_snapshot(self):
        with self.lock:
            return self.snapshot

    def get_history(self):
        with self.lock:
            return list(self.history)

    def get_processes(self):
        with self.lock:
            return self.processes

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

import psutil

GB = 2**30

METRIC_NAMES = ("cpu", "ram", "gpu", "disk", "net")


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: int | None
    ram_percent: int | None
    ram_used_gb: float | None
    ram_total_gb: float | None
    gpu_percent: int | None
    disk_percent: int | None
    disk_used_gb: float | None
    disk_total_gb: float | None
    disk_io_mbps: float | None
    net_upload_mbps: float | None
    net_download_mbps: float | None


class SysInfoSampler:
    def __init__(self) -> None:
        psutil.cpu_percent(interval=None)  # prime; first call is meaningless
        self._prev_disk_io = psutil.disk_io_counters()
        self._prev_net_io = psutil.net_io_counters()
        self._prev_time = time.monotonic()
        self._gpu_available: bool | None = None
        self._last_gpu_sample = 0.0
        self._last_gpu_percent: int | None = None

    def sample(self, now: float, gpu_sample_seconds: int = 5) -> SystemMetrics:
        cpu_percent = round(psutil.cpu_percent(interval=None))

        vm = psutil.virtual_memory()
        ram_percent = round(vm.percent)
        ram_used_gb = vm.used / GB
        ram_total_gb = vm.total / GB

        du = psutil.disk_usage("/")
        disk_percent = round(du.percent)
        disk_used_gb = du.used / GB
        disk_total_gb = du.total / GB

        elapsed = now - self._prev_time

        disk_io = psutil.disk_io_counters()
        disk_io_mbps = self._rate(
            disk_io, self._prev_disk_io,
            lambda c: c.read_bytes + c.write_bytes,
            elapsed,
        )
        self._prev_disk_io = disk_io

        net_io = psutil.net_io_counters()
        net_upload_mbps = self._rate(net_io, self._prev_net_io, lambda c: c.bytes_sent, elapsed)
        net_download_mbps = self._rate(net_io, self._prev_net_io, lambda c: c.bytes_recv, elapsed)
        self._prev_net_io = net_io

        self._prev_time = now

        gpu_percent = self._sample_gpu(now, gpu_sample_seconds)

        return SystemMetrics(
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            ram_used_gb=ram_used_gb,
            ram_total_gb=ram_total_gb,
            gpu_percent=gpu_percent,
            disk_percent=disk_percent,
            disk_used_gb=disk_used_gb,
            disk_total_gb=disk_total_gb,
            disk_io_mbps=disk_io_mbps,
            net_upload_mbps=net_upload_mbps,
            net_download_mbps=net_download_mbps,
        )

    @staticmethod
    def _rate(current, previous, accessor, elapsed: float) -> float | None:
        if current is None or previous is None or elapsed <= 0:
            return None
        return (accessor(current) - accessor(previous)) / elapsed / 1e6

    def _sample_gpu(self, now: float, gpu_sample_seconds: int) -> int | None:
        if self._gpu_available is False:
            return None
        if self._gpu_available is True and now - self._last_gpu_sample < gpu_sample_seconds:
            return self._last_gpu_percent

        if self._gpu_available is None and shutil.which("nvidia-smi") is None:
            self._gpu_available = False
            return None

        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=1,
                check=True,
            )
            percent = round(float(result.stdout.strip().splitlines()[0]))
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError, OSError, IndexError):
            self._gpu_available = False
            self._last_gpu_percent = None
            return None

        self._gpu_available = True
        self._last_gpu_sample = now
        self._last_gpu_percent = percent
        return percent


def next_metric_index(enabled: list[str], index: int) -> int:
    if not enabled:
        return 0
    return index % len(enabled)


def format_metric_lines(
    name: str | None, m: SystemMetrics, ram_mode: str, disk_mode: str
) -> tuple[str, str]:
    if name == "cpu":
        return "CPU", _pct(m.cpu_percent)

    if name == "ram":
        if ram_mode == "used_total":
            return "RAM", _used_total(m.ram_used_gb, m.ram_total_gb)
        return "RAM", _pct(m.ram_percent)

    if name == "gpu":
        return "GPU", _pct(m.gpu_percent)

    if name == "disk":
        if disk_mode == "used_total":
            return "Disk", _used_total(m.disk_used_gb, m.disk_total_gb)
        if disk_mode == "io_speed":
            return "Disk I/O", _mbps(m.disk_io_mbps)
        return "Disk", _pct(m.disk_percent)

    if name == "net":
        if m.net_upload_mbps is None or m.net_download_mbps is None:
            return "Net MB/s", "--"
        return "Net MB/s", f"U{m.net_upload_mbps:.1f} D{m.net_download_mbps:.1f}"

    return "System", "No metrics"


def _pct(value: int | None) -> str:
    return f"{value}%" if value is not None else "--"


def _used_total(used_gb: float | None, total_gb: float | None) -> str:
    if used_gb is None or total_gb is None:
        return "--"
    return f"{used_gb:.1f}/{total_gb:.1f}GB"


def _mbps(value: float | None) -> str:
    return f"{value:.1f}MB/s" if value is not None else "--"

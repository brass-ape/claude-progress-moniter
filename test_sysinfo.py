from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sysinfo import SystemMetrics, SysInfoSampler, format_metric_lines, next_metric_index


def _metrics(**overrides) -> SystemMetrics:
    base = dict(
        cpu_percent=42,
        ram_percent=61,
        ram_used_gb=9.8,
        ram_total_gb=16.0,
        gpu_percent=12,
        disk_percent=55,
        disk_used_gb=210.4,
        disk_total_gb=512.0,
        disk_io_mbps=3.2,
        net_upload_mbps=0.4,
        net_download_mbps=2.1,
    )
    base.update(overrides)
    return SystemMetrics(**base)


class FormatMetricLinesTests(unittest.TestCase):
    def test_cpu(self) -> None:
        self.assertEqual(format_metric_lines("cpu", _metrics(), "percent", "percent"), ("CPU", "42%"))

    def test_ram_percent(self) -> None:
        self.assertEqual(format_metric_lines("ram", _metrics(), "percent", "percent"), ("RAM", "61%"))

    def test_ram_used_total(self) -> None:
        self.assertEqual(format_metric_lines("ram", _metrics(), "used_total", "percent"), ("RAM", "9.8/16.0GB"))

    def test_gpu_available(self) -> None:
        self.assertEqual(format_metric_lines("gpu", _metrics(), "percent", "percent"), ("GPU", "12%"))

    def test_gpu_unavailable(self) -> None:
        self.assertEqual(format_metric_lines("gpu", _metrics(gpu_percent=None), "percent", "percent"), ("GPU", "--"))

    def test_disk_percent(self) -> None:
        self.assertEqual(format_metric_lines("disk", _metrics(), "percent", "percent"), ("Disk", "55%"))

    def test_disk_used_total(self) -> None:
        self.assertEqual(
            format_metric_lines("disk", _metrics(), "percent", "used_total"), ("Disk", "210.4/512.0GB")
        )

    def test_disk_io_speed(self) -> None:
        self.assertEqual(format_metric_lines("disk", _metrics(), "percent", "io_speed"), ("Disk I/O", "3.2MB/s"))

    def test_disk_io_speed_unavailable(self) -> None:
        self.assertEqual(
            format_metric_lines("disk", _metrics(disk_io_mbps=None), "percent", "io_speed"), ("Disk I/O", "--")
        )

    def test_net(self) -> None:
        self.assertEqual(format_metric_lines("net", _metrics(), "percent", "percent"), ("Net MB/s", "U0.4 D2.1"))

    def test_net_unavailable(self) -> None:
        m = _metrics(net_upload_mbps=None, net_download_mbps=None)
        self.assertEqual(format_metric_lines("net", m, "percent", "percent"), ("Net MB/s", "--"))

    def test_unknown_name(self) -> None:
        self.assertEqual(format_metric_lines(None, _metrics(), "percent", "percent"), ("System", "No metrics"))
        self.assertEqual(format_metric_lines("bogus", _metrics(), "percent", "percent"), ("System", "No metrics"))

    def test_all_lines_fit_lcd_row1_budget(self) -> None:
        """Row 1 has a 15-char budget (col 15 is reserved for the status indicator)."""
        for name in ("cpu", "ram", "gpu", "disk", "net"):
            for ram_mode in ("percent", "used_total"):
                for disk_mode in ("percent", "used_total", "io_speed"):
                    _, line1 = format_metric_lines(name, _metrics(), ram_mode, disk_mode)
                    self.assertLessEqual(len(line1), 15, f"{name}/{ram_mode}/{disk_mode}: {line1!r}")


class NextMetricIndexTests(unittest.TestCase):
    def test_empty_list_returns_zero(self) -> None:
        self.assertEqual(next_metric_index([], 5), 0)

    def test_round_robins(self) -> None:
        enabled = ["cpu", "ram", "gpu"]
        self.assertEqual(next_metric_index(enabled, 0), 0)
        self.assertEqual(next_metric_index(enabled, 1), 1)
        self.assertEqual(next_metric_index(enabled, 3), 0)
        self.assertEqual(next_metric_index(enabled, 4), 1)


def _counters(**kwargs):
    fields = dict(read_bytes=0, write_bytes=0, bytes_sent=0, bytes_recv=0)
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class SysInfoSamplerTests(unittest.TestCase):
    def _make_sampler(self):
        with (
            patch("sysinfo.psutil.cpu_percent", return_value=0.0),
            patch("sysinfo.psutil.disk_io_counters", return_value=_counters(read_bytes=1000, write_bytes=1000)),
            patch("sysinfo.psutil.net_io_counters", return_value=_counters(bytes_sent=1000, bytes_recv=2000)),
        ):
            sampler = SysInfoSampler()
        sampler._prev_time = 0.0  # deterministic elapsed-time baseline for delta math
        return sampler

    def test_field_mapping(self) -> None:
        sampler = self._make_sampler()
        vm = SimpleNamespace(percent=61.4, used=9.8 * 2**30, total=16 * 2**30)
        du = SimpleNamespace(percent=55.2, used=210.4 * 2**30, total=512 * 2**30)
        with (
            patch("sysinfo.psutil.cpu_percent", return_value=42.3),
            patch("sysinfo.psutil.virtual_memory", return_value=vm),
            patch("sysinfo.psutil.disk_usage", return_value=du),
            patch("sysinfo.psutil.disk_io_counters", return_value=_counters(read_bytes=1000, write_bytes=1000)),
            patch("sysinfo.psutil.net_io_counters", return_value=_counters(bytes_sent=1000, bytes_recv=2000)),
            patch("sysinfo.shutil.which", return_value=None),
        ):
            metrics = sampler.sample(now=100.0)
        self.assertEqual(metrics.cpu_percent, 42)
        self.assertEqual(metrics.ram_percent, 61)
        self.assertAlmostEqual(metrics.ram_used_gb, 9.8, places=3)
        self.assertEqual(metrics.disk_percent, 55)
        self.assertIsNone(metrics.gpu_percent)

    def test_delta_based_io_rates(self) -> None:
        sampler = self._make_sampler()
        vm = SimpleNamespace(percent=0.0, used=0, total=1)
        du = SimpleNamespace(percent=0.0, used=0, total=1)
        # 2,000,000 bytes of combined disk I/O and 1,000,000 bytes each way of
        # network I/O over 1 second -> 2.0 MB/s disk, 1.0 MB/s up and down.
        disk_io = _counters(read_bytes=1_000_000 + 1000, write_bytes=1_000_000 + 1000)
        net_io = _counters(bytes_sent=1_000_000 + 1000, bytes_recv=1_000_000 + 2000)
        with (
            patch("sysinfo.psutil.cpu_percent", return_value=0.0),
            patch("sysinfo.psutil.virtual_memory", return_value=vm),
            patch("sysinfo.psutil.disk_usage", return_value=du),
            patch("sysinfo.psutil.disk_io_counters", return_value=disk_io),
            patch("sysinfo.psutil.net_io_counters", return_value=net_io),
            patch("sysinfo.shutil.which", return_value=None),
        ):
            metrics = sampler.sample(now=1.0)
        self.assertAlmostEqual(metrics.disk_io_mbps, 2.0, places=3)
        self.assertAlmostEqual(metrics.net_upload_mbps, 1.0, places=3)
        self.assertAlmostEqual(metrics.net_download_mbps, 1.0, places=3)

    def test_gpu_unavailable_when_no_nvidia_smi(self) -> None:
        sampler = self._make_sampler()
        vm = SimpleNamespace(percent=0.0, used=0, total=1)
        du = SimpleNamespace(percent=0.0, used=0, total=1)
        with (
            patch("sysinfo.psutil.cpu_percent", return_value=0.0),
            patch("sysinfo.psutil.virtual_memory", return_value=vm),
            patch("sysinfo.psutil.disk_usage", return_value=du),
            patch("sysinfo.psutil.disk_io_counters", return_value=_counters()),
            patch("sysinfo.psutil.net_io_counters", return_value=_counters()),
            patch("sysinfo.shutil.which", return_value=None) as which,
        ):
            metrics = sampler.sample(now=1.0)
            sampler.sample(now=2.0)
        self.assertIsNone(metrics.gpu_percent)
        which.assert_called_once()  # resolved once, never rechecked

    def test_gpu_available_uses_subprocess_and_caches(self) -> None:
        sampler = self._make_sampler()
        vm = SimpleNamespace(percent=0.0, used=0, total=1)
        du = SimpleNamespace(percent=0.0, used=0, total=1)
        proc_result = SimpleNamespace(stdout="37\n")
        with (
            patch("sysinfo.psutil.cpu_percent", return_value=0.0),
            patch("sysinfo.psutil.virtual_memory", return_value=vm),
            patch("sysinfo.psutil.disk_usage", return_value=du),
            patch("sysinfo.psutil.disk_io_counters", return_value=_counters()),
            patch("sysinfo.psutil.net_io_counters", return_value=_counters()),
            patch("sysinfo.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("sysinfo.subprocess.run", return_value=proc_result) as run,
        ):
            first = sampler.sample(now=1.0, gpu_sample_seconds=5)
            second = sampler.sample(now=2.0, gpu_sample_seconds=5)  # within cadence window
        self.assertEqual(first.gpu_percent, 37)
        self.assertEqual(second.gpu_percent, 37)
        run.assert_called_once()  # cadence caching: not re-invoked within the window


if __name__ == "__main__":
    unittest.main()

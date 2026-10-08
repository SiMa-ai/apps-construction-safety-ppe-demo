"""Bounded timing samples and explicit throughput for cross-platform comparisons."""

import os
import platform
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np


class Performance:
    def __init__(self, warmup_frames=30, window=300):
        self.warmup_frames = warmup_frames
        self.window = window
        self.samples = defaultdict(lambda: deque(maxlen=window))
        self.completed = deque(maxlen=window + 1)
        self.frames = 0
        self.started = None
        self.previous_wall = time.monotonic()
        self.previous_cpu = time.process_time()

    def record(self, timings, completed_at):
        self.frames += 1
        if self.frames <= self.warmup_frames:
            return
        if self.started is None:
            self.started = completed_at
        self.completed.append(completed_at)
        for stage, seconds in timings.items():
            if seconds is not None:
                self.samples[stage].append(seconds * 1000)

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        cpu = time.process_time()
        wall_elapsed = now - self.previous_wall
        cpu_percent = 100 * (cpu - self.previous_cpu) / wall_elapsed if wall_elapsed > 0 else None
        self.previous_wall, self.previous_cpu = now, cpu
        duration = self.completed[-1] - self.completed[0] if len(self.completed) > 1 else 0
        stages = {
            name: {
                "samples": len(values),
                "mean_ms": round(float(np.mean(values)), 2),
                "p50_ms": round(float(np.percentile(values, 50)), 2),
                "p95_ms": round(float(np.percentile(values, 95)), 2),
            }
            for name, values in self.samples.items() if values
        }
        rss = None
        try:
            resident_pages = int(Path('/proc/self/statm').read_text().split()[1])
            rss = round(resident_pages * os.sysconf('SC_PAGE_SIZE') / 1024**2, 1)
        except (OSError, ValueError, IndexError):
            pass
        return {
            "schema_version": 1,
            "host": platform.node(),
            "architecture": platform.machine(),
            "warmup_frames": self.warmup_frames,
            "warmup_complete": self.frames > self.warmup_frames,
            "measured_frames": max(0, self.frames - self.warmup_frames),
            "window_frames": len(self.completed),
            "processing_fps": round((len(self.completed) - 1) / duration, 2) if duration > 0 else None,
            "stages": stages,
            "process_cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
            "process_rss_mb": rss,
            "timing_scope": "Host wall time, including preprocessing/decoding; not MLA kernel time",
            "end_to_end_latency_ms": None,
        }

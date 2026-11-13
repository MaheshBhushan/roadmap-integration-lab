"""Per-stage wall time and peak resident memory of the pipeline process (Linux procfs).

Peaks come from the kernel's RSS high-water mark `VmHWM` in /proc/self/status, which is exact (no
sampling). Before each stage the mark is reset to the current RSS by writing "5" to
/proc/self/clear_refs, so every stage reports its own peak. getrusage(ru_maxrss) is not used because it
survives execve and can report the peak of the process that spawned the interpreter.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_STATUS = Path("/proc/self/status")
_CLEAR_REFS = Path("/proc/self/clear_refs")
MIB = 2**20


def _status_kib(field: str) -> int | None:
    try:
        for line in _STATUS.read_text().splitlines():
            if line.startswith(field + ":"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def rss_mib() -> float | None:
    kib = _status_kib("VmRSS")
    return None if kib is None else round(kib / 1024, 1)


def hwm_mib() -> float | None:
    kib = _status_kib("VmHWM")
    return None if kib is None else round(kib / 1024, 1)


def reset_hwm() -> bool:
    try:
        _CLEAR_REFS.write_text("5")
        return True
    except OSError:
        return False


class StageProfiler:
    def __init__(self) -> None:
        self.stages: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()
        self._total: float | None = None
        self.per_stage_reset = True

    def __enter__(self) -> StageProfiler:
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self._total = time.perf_counter() - self._t0

    @contextmanager
    def stage(self, name: str):
        self.per_stage_reset &= reset_hwm()
        rss_start = rss_mib()
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stages.append({
                "stage": name,
                "seconds": round(time.perf_counter() - start, 3),
                "rss_start_mib": rss_start,
                "rss_end_mib": rss_mib(),
                "peak_rss_mib": hwm_mib(),
            })

    def report(self) -> dict[str, Any]:
        total = self._total if self._total is not None else time.perf_counter() - self._t0
        peaks = [s["peak_rss_mib"] for s in self.stages if s["peak_rss_mib"] is not None]
        return {
            "total_seconds": round(total, 3),
            "peak_rss_mib": max(peaks) if peaks else None,
            "per_stage_reset": self.per_stage_reset,
            "stages": self.stages,
        }

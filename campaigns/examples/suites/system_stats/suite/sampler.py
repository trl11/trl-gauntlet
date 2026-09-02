"""One pass over every reader, and the state that turns counters into rates.

``/proc/stat`` and ``/proc/net/dev`` count since boot, so :class:`Sampler`
keeps the previous read and the time it was taken and reports the difference.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from suite.cpu import CpuUsage, LoadAverage, Stat, cpu_usage, read_loadavg, read_stat
from suite.disks import Disk, read_disks
from suite.host import read_process_count, read_uptime
from suite.memory import Memory, read_meminfo
from suite.network import NetCounters, net_deltas, read_net_dev
from suite.procfs import PROC, SYS
from suite.thermal import ThermalZone, read_thermal

# Utilisation over a window this short is dominated by the jiffy granularity of
# /proc/stat, so the sampler waits for at least this much of one.
DEFAULT_MIN_WINDOW_S = 0.1


@dataclass(frozen=True)
class Sample:
    """Everything read in one tick. Any field may be ``None`` or empty."""

    context_switches_per_s: float | None
    cpu: CpuUsage | None
    cpu_count: int
    disks: tuple[Disk, ...]
    load: LoadAverage | None
    memory: Memory | None
    network: dict[str, NetCounters] = field(default_factory=dict)
    network_growth: dict[str, NetCounters] = field(default_factory=dict)
    process_count: int | None = None
    procs_blocked: int | None = None
    procs_running: int | None = None
    thermal: tuple[ThermalZone, ...] = ()
    uptime_s: float | None = None
    window_s: float = 0.0

    @property
    def hottest(self) -> ThermalZone | None:
        """The thermal zone reporting the highest temperature."""
        return max(self.thermal, key=lambda zone: zone.celsius) if self.thermal else None

    @property
    def load_per_core(self) -> float | None:
        """One-minute load average divided by the number of cores."""
        if self.load is None or self.cpu_count <= 0:
            return None
        return self.load.one / self.cpu_count

    @property
    def tightest_disk(self) -> Disk | None:
        """The mounted filesystem with the least free space, as a share."""
        return min(self.disks, key=lambda disk: disk.free_percent) if self.disks else None


class Sampler:
    """Reads every statistic, deriving rates from the previous read.

    ``proc`` and ``sys_root`` are the filesystem roots to read from, so a test
    points the whole sampler at a fixture tree.
    """

    def __init__(
        self,
        *,
        proc: Path = PROC,
        sys_root: Path = SYS,
        min_window_s: float = DEFAULT_MIN_WINDOW_S,
    ) -> None:
        self._proc = proc
        self._sys_root = sys_root
        self._min_window_s = min_window_s
        self._previous_stat: Stat | None = None
        self._previous_net: dict[str, NetCounters] = {}
        self._previous_at: float | None = None

    def prime(self) -> None:
        """Take the first read, so the next :meth:`sample` has deltas to report."""
        self.sample()

    def sample(self) -> Sample:
        """Read everything once and fold it against the previous read."""
        self._await_window()
        now = time.monotonic()
        window = now - self._previous_at if self._previous_at is not None else 0.0

        stat = read_stat(proc=self._proc)
        network = read_net_dev(proc=self._proc)
        sample = Sample(
            context_switches_per_s=self._switch_rate(stat, window),
            cpu=cpu_usage(self._previous_stat, stat) if self._previous_stat and stat else None,
            cpu_count=len(stat.per_core) if stat and stat.per_core else (os.cpu_count() or 0),
            disks=read_disks(proc=self._proc),
            load=read_loadavg(proc=self._proc),
            memory=read_meminfo(proc=self._proc),
            network=network,
            network_growth=net_deltas(self._previous_net, network),
            process_count=read_process_count(proc=self._proc),
            procs_blocked=stat.procs_blocked if stat else None,
            procs_running=stat.procs_running if stat else None,
            thermal=read_thermal(sys_root=self._sys_root),
            uptime_s=read_uptime(proc=self._proc),
            window_s=round(window, 4),
        )
        self._previous_stat = stat or self._previous_stat
        self._previous_net = network or self._previous_net
        self._previous_at = now
        return sample

    def _await_window(self) -> None:
        if self._previous_at is None or self._min_window_s <= 0:
            return
        remaining = self._min_window_s - (time.monotonic() - self._previous_at)
        if remaining > 0:
            time.sleep(remaining)

    def _switch_rate(self, stat: Stat | None, window: float) -> float | None:
        if stat is None or self._previous_stat is None or window <= 0:
            return None
        delta = stat.context_switches - self._previous_stat.context_switches
        return round(delta / window, 2) if delta >= 0 else None

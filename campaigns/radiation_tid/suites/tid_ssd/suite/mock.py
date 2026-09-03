"""A synthesised SSD for runs with no hardware attached.

Shapes its output exactly like ``probe.sh`` on a real unit, so the analysis,
the anomaly rules and the verdict all run over mock data unchanged. This is
what `gauntlet verify --run` executes, and what lets the campaign hold a green
baseline for a part that has not arrived yet.

The disk degrades with accumulated ticks in the order a real one does under
dose: bandwidth sags first as the controller spends longer on retries, then
the media error count starts moving, then the spare pool drains, then blocks
come back corrupt, and finally the drive leaves the bus. A short run stays
healthy, so the conformance profile passes.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from suite.profile import Device

# Ticks of exposure before each symptom appears. Well beyond a conformance
# run, so `smoke.yaml` never trips one.
BANDWIDTH_ONSET = 40
MEDIA_ERROR_ONSET = 80
SPARE_ONSET = 120
MISCOMPARE_ONSET = 160
DROPOUT_ONSET = 220

BASELINE_WRITE_MBPS = 2400.0
BASELINE_READ_MBPS = 3100.0


def _rng(iteration: int, salt: str) -> random.Random:
    """A generator that depends only on the tick, so a run is reproducible."""
    return random.Random(f"tid_ssd:{salt}:{iteration}")


def _offset(salt: str) -> int:
    """A steady per-drive difference, so a fanned-out run is four parts and not one."""
    return sum(ord(c) for c in salt) % 200


def _degradation(iteration: int, onset: int) -> float:
    """How far past a symptom's onset this tick is, from 0.0 upwards."""
    return max(0.0, (iteration - onset) / 40.0)


def _digests(iteration: int, device: Device, corrupt: bool) -> tuple[str, str]:
    """The pair of hashes the write-verify compares."""
    written = hashlib.sha256(f"{device.name}:{iteration}".encode()).hexdigest()
    if not corrupt:
        return written, written
    return written, hashlib.sha256(f"{device.name}:{iteration}:rot".encode()).hexdigest()


def probe(iteration: int, device: Device, unit: str = "uut") -> dict[str, Any]:
    """One probe result, as the unit would have printed it.

    ``unit`` only shifts the numbers, never when a symptom arrives: a fanned-out
    run shows four distinguishable drives degrading on the same schedule.
    """
    rng = _rng(iteration, f"{unit}:{device.name}")
    salt = _offset(f"{unit}:{device.name}")
    present = iteration < DROPOUT_ONSET

    sag = _degradation(iteration, BANDWIDTH_ONSET) * 400.0
    write = max(0.0, BASELINE_WRITE_MBPS - salt - sag - rng.uniform(0.0, 20.0))
    read = max(0.0, BASELINE_READ_MBPS - salt - sag - rng.uniform(0.0, 20.0))

    corrupt = _degradation(iteration, MISCOMPARE_ONSET) > 0 and rng.random() < 0.25
    expected, actual = _digests(iteration, device, corrupt)

    spare = max(0, 100 - int(_degradation(iteration, SPARE_ONSET) * 30.0))
    smart = {
        "available_spare": spare,
        "available_spare_threshold": 10,
        "critical_warning": 1 if spare < 10 else 0,
        "media_errors": int(_degradation(iteration, MEDIA_ERROR_ONSET) * 12.0),
        "num_err_log_entries": int(_degradation(iteration, MEDIA_ERROR_ONSET) * 20.0),
        "percentage_used": min(100, int(_degradation(iteration, SPARE_ONSET) * 8.0)),
        "temperature": 312 + int(_degradation(iteration, BANDWIDTH_ONSET) * 4.0),
        "unsafe_shutdowns": 0,
    }

    if not present:
        return {
            "device_present": False,
            "write_mbps": None,
            "read_mbps": None,
            "verify_ok": None,
            "verify_expected_sha": None,
            "verify_actual_sha": None,
            "cache_drop_failed": False,
            "smart": {},
            "error": "device_missing;",
        }

    return {
        "device_present": True,
        "write_mbps": round(write, 1),
        "read_mbps": round(read, 1),
        "verify_ok": not corrupt,
        "verify_expected_sha": expected,
        "verify_actual_sha": actual,
        "cache_drop_failed": False,
        "smart": smart,
        "error": None,
    }


def kernel_log(iteration: int, device: Device) -> list[str]:
    """Kernel messages a degrading drive would have logged this tick."""
    if _degradation(iteration, MEDIA_ERROR_ONSET) <= 0:
        return []
    if iteration >= DROPOUT_ONSET:
        return [f"nvme nvme0: Removing after probe failure ({device.device})"]
    return [f"blk_update_request: I/O error, dev {device.device.split('/')[-1]}, sector {iteration * 4096}"]

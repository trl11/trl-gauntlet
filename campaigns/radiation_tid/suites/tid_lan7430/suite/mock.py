"""A synthesised LAN7430 for runs with no hardware attached.

Shapes its output exactly like the collector on a real unit, so the analysis,
the anomaly rules and the verdict all run over mock data unchanged. This is
what `gauntlet verify --run` executes, and what lets the campaign hold a green
baseline for a part that has not arrived yet.

The part degrades slowly with accumulated ticks, in the way a real one does
under dose: throughput sags first, then error counters start moving, and the
OTP goes last. A short run stays healthy, so the conformance profile passes.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

# Ticks of exposure before each symptom appears. Well beyond a conformance
# run, so `smoke.yaml` never trips one.
THROUGHPUT_ONSET = 40
COUNTER_ONSET = 80
OTP_ONSET = 160

BASELINE_MBPS = 941.0
BASELINE_MAC = "00:80:0f:1a:2b:3c"


def _rng(iteration: int, salt: str) -> random.Random:
    """A generator that depends only on the tick, so a run is reproducible."""
    return random.Random(f"lan7430:{salt}:{iteration}")


def _degradation(iteration: int, onset: int) -> float:
    """How far past a symptom's onset this tick is, from 0.0 upwards."""
    return max(0.0, (iteration - onset) / 40.0)


def throughput(iteration: int) -> dict[str, dict[str, float]]:
    """Synthetic TCP and UDP results for one tick."""
    rng = _rng(iteration, "throughput")
    sag = _degradation(iteration, THROUGHPUT_ONSET) * 60.0
    tx = max(0.0, BASELINE_MBPS - sag - rng.uniform(0.0, 4.0))
    rx = max(0.0, BASELINE_MBPS - sag - rng.uniform(0.0, 4.0))
    loss = min(100.0, _degradation(iteration, COUNTER_ONSET) * 3.0 + rng.uniform(0.0, 0.05))
    return {
        "tcp_tx": {"mbps": round(tx, 3), "retransmits": float(int(sag)), "seconds": 5.0},
        "tcp_rx": {"mbps": round(rx, 3), "retransmits": 0.0, "seconds": 5.0},
        "udp": {
            "jitter_ms": round(0.05 + _degradation(iteration, COUNTER_ONSET) * 0.4, 4),
            "loss_pct": round(loss, 4),
            "lost_packets": float(int(loss * 100)),
            "mbps": round(min(tx, 900.0), 3),
            "packets": 76000.0,
        },
    }


def sample(iteration: int, interface: str) -> dict[str, Any]:
    """One collector sample, as the unit would have printed it."""
    rng = _rng(iteration, "sample")
    errors = int(_degradation(iteration, COUNTER_ONSET) * 12.0)
    correctable = int(_degradation(iteration, COUNTER_ONSET) * 30.0)
    packets = 100000 + iteration * 76000

    # Past its onset the image reads back differently every tick, which is
    # what a bit flipping in the OTP looks like from the outside.
    otp_seed = "golden" if iteration < OTP_ONSET else f"flipped:{iteration}"
    registers_seed = "golden" if iteration < COUNTER_ONSET else f"drifted:{iteration // 20}"

    return {
        "driver": {
            "bus_info": "0000:01:00.0",
            "driver": "lan743x",
            "firmware_version": "N/A",
            "version": "6.6.0",
        },
        "errors": {},
        "ethtool_stats": {"RX FCS Errors": errors, "TX Total Frames": packets},
        "interface": interface,
        "link": {
            "address": BASELINE_MAC,
            "carrier": 1,
            "carrier_changes": 1 + int(_degradation(iteration, COUNTER_ONSET)),
            "carrier_down_count": int(_degradation(iteration, COUNTER_ONSET)),
            "duplex": "full",
            "mtu": 1500,
            "operstate": "up",
            "speed_mbps": 1000,
        },
        "otp": {
            "bytes": 512,
            "hex": "",
            "sha256": hashlib.sha256(otp_seed.encode()).hexdigest(),
        },
        "pcie": {
            "aer": {
                "correctable.RxErr": correctable,
                "fatal.TOTAL_ERR_FATAL": 0,
                "nonfatal.TOTAL_ERR_NONFATAL": 0,
            },
            "current_link_speed": "5.0 GT/s PCIe",
            "current_link_width": 1,
            "max_link_speed": "5.0 GT/s PCIe",
            "max_link_width": 1,
            "present": True,
            "slot": "0000:01:00.0",
        },
        "present": True,
        "registers": {"bytes": 1024, "sha256": hashlib.sha256(registers_seed.encode()).hexdigest()},
        "statistics": {
            "rx_bytes": packets * 1500,
            "rx_crc_errors": errors,
            "rx_dropped": 0,
            "rx_errors": errors,
            "rx_packets": packets,
            "tx_bytes": packets * 1500,
            "tx_dropped": 0,
            "tx_errors": 0,
            "tx_packets": packets,
        },
        "temperature_c": {"cpu-thermal": round(46.0 + rng.uniform(-0.5, 0.5), 2)},
        "dmesg": {"cursor": 100.0 + iteration, "lines": [], "total": 0},
    }

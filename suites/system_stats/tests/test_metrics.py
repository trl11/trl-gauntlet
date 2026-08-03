"""The metrics record built from one sample."""

from __future__ import annotations

from collections.abc import Callable

from suite.cpu import CpuUsage, LoadAverage
from suite.disks import Disk
from suite.memory import Memory
from suite.metrics import slug, to_metrics
from suite.network import NetCounters
from suite.sampler import Sample
from suite.thermal import ThermalZone


def _counters(**overrides: int) -> NetCounters:
    fields: dict[str, int] = {
        "rx_bytes": 0,
        "rx_dropped": 0,
        "rx_errors": 0,
        "rx_packets": 0,
        "tx_bytes": 0,
        "tx_dropped": 0,
        "tx_errors": 0,
        "tx_packets": 0,
    }
    return NetCounters(name="eth0", **{**fields, **overrides})


def test_slug_makes_a_metric_key() -> None:
    assert slug("/") == "root"
    assert slug("/workspaces/gauntlet") == "workspaces_gauntlet"
    assert slug("x86_pkg_temp") == "x86_pkg_temp"


def test_to_metrics_reports_every_reading(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(
        context_switches_per_s=1200.0,
        cpu=CpuUsage(overall_percent=12.5, per_core_percent={"cpu0": 25.0}),
        cpu_count=2,
        disks=(
            Disk(
                device="/dev/fixture",
                filesystem="ext4",
                free_bytes=250,
                mount_point="/",
                total_bytes=1000,
                used_bytes=700,
            ),
        ),
        load=LoadAverage(fifteen=0.5, five=0.4, one=1.0, runnable=2, total=100),
        memory=Memory(
            available_bytes=600,
            buffers_bytes=0,
            cached_bytes=0,
            free_bytes=500,
            swap_free_bytes=50,
            swap_total_bytes=100,
            total_bytes=1000,
        ),
        network={"eth0": _counters(rx_bytes=10, rx_dropped=1, rx_errors=2, rx_packets=3, tx_bytes=10)},
        network_growth={"eth0": _counters(rx_bytes=200, rx_dropped=1, rx_errors=2, rx_packets=3, tx_bytes=100)},
        process_count=42,
        procs_blocked=0,
        procs_running=2,
        thermal=(ThermalZone(celsius=42.5, label="x86_pkg_temp", name="thermal_zone0"),),
        uptime_s=1234.56,
        window_s=2.0,
    )

    metrics = to_metrics(sample)

    assert metrics["cpu"] == {"percent": 12.5, "per_core": {"cpu0": 25.0}}
    assert metrics["context_switches_per_s"] == 1200.0
    assert metrics["load"]["per_core"] == 0.5
    assert metrics["memory"]["available_percent"] == 60.0
    assert metrics["swap"]["used_percent"] == 50.0
    assert metrics["disk"]["root"]["free_percent"] == 25.0
    assert metrics["thermal"] == {"x86_pkg_temp": 42.5}
    assert metrics["thermal_max_c"] == 42.5
    assert metrics["net"]["eth0"]["new_errors"] == 2
    assert metrics["net"]["eth0"]["rx_bytes_per_s"] == 100.0
    assert metrics["processes"] == {"count": 42, "running": 2, "blocked": 0}
    assert metrics["uptime_s"] == 1234.6


def test_to_metrics_omits_readings_the_host_did_not_offer(make_sample: Callable[..., Sample]) -> None:
    metrics = to_metrics(make_sample())

    assert set(metrics) == {"cpu_count", "window_s"}


def test_to_metrics_reports_a_zero_rate_without_a_window(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(
        network={"eth0": _counters(rx_bytes=10, rx_packets=1, tx_bytes=10, tx_packets=1)},
        network_growth={"eth0": _counters(rx_bytes=10, rx_packets=1, tx_bytes=10, tx_packets=1)},
        window_s=0.0,
    )

    assert to_metrics(sample)["net"]["eth0"]["rx_bytes_per_s"] == 0.0


def test_to_metrics_reports_a_zero_rate_before_the_first_delta(make_sample: Callable[..., Sample]) -> None:
    sample = make_sample(network={"eth0": _counters(rx_bytes=10, rx_errors=7)}, network_growth={})

    assert to_metrics(sample)["net"]["eth0"] == {
        "dropped": 0,
        "errors": 7,
        "new_dropped": 0,
        "new_errors": 0,
        "rx_bytes_per_s": 0.0,
        "tx_bytes_per_s": 0.0,
    }

"""Host information and telemetry, including where the kernel files are absent."""

from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet.api import host_stats


@pytest.fixture
def missing_proc(monkeypatch, tmp_path: Path):
    """Point the kernel readers at paths that do not exist."""
    monkeypatch.setattr(host_stats, "_PROC", tmp_path / "no-proc")
    monkeypatch.setattr(host_stats, "_THERMAL", tmp_path / "no-thermal")


class TestSystemInfo:
    def test_reports_the_host(self, client) -> None:
        body = client.get("/api/system/info").json()
        assert set(body) == {
            "arch",
            "boot_time",
            "contract_version",
            "cpu_count",
            "cpu_model",
            "gauntlet",
            "gauntlet_sdk",
            "hostname",
            "kernel",
            "memory_total_bytes",
            "os",
            "python",
        }
        assert body["python"].count(".") == 2

    def test_survives_a_host_without_proc(self, client, missing_proc) -> None:
        body = client.get("/api/system/info").json()
        assert body["boot_time"] is None
        assert body["cpu_model"] is None
        assert body["memory_total_bytes"] is None
        assert body["hostname"]


class TestSystemData:
    def test_reports_a_sample(self, client) -> None:
        body = client.get("/api/system/data").json()
        assert set(body) == {
            "cpu_per_core",
            "cpu_percent",
            "disk",
            "disks",
            "interfaces",
            "load_avg",
            "memory",
            "process_count",
            "swap",
            "temperatures",
            "uptime_s",
        }
        assert set(body["memory"]) == {"available", "percent", "total", "used"}
        assert set(body["swap"]) == {"percent", "total", "used"}
        assert all(set(disk) == {"free", "mount", "percent", "total", "used"} for disk in body["disks"])
        assert all(set(reading) == {"celsius", "label"} for reading in body["temperatures"])

    def test_cpu_percent_needs_two_samples(self, client) -> None:
        assert client.get("/api/system/data").json()["cpu_percent"] is None
        second = client.get("/api/system/data").json()
        assert 0.0 <= second["cpu_percent"] <= 100.0
        assert len(second["cpu_per_core"]) >= 1

    def test_survives_a_host_without_proc(self, client, missing_proc) -> None:
        client.get("/api/system/data")
        body = client.get("/api/system/data").json()
        assert body["cpu_percent"] is None
        assert body["cpu_per_core"] == []
        assert body["memory"] == {"total": None, "available": None, "used": None, "percent": None}
        assert body["swap"] == {"total": None, "used": None, "percent": None}
        assert body["temperatures"] == []
        assert body["uptime_s"] is None
        assert body["process_count"] is None
        # disk_usage("/") still answers, so the root filesystem is reported.
        assert [disk["mount"] for disk in body["disks"]] == ["/"]


class TestRunsDisk:
    def test_system_data_names_the_disk_the_runs_are_written_to(self, client):
        payload = client.get("/api/system/data").json()

        disk = payload["disk"]
        assert disk is not None
        assert str(client.app.state.settings.runs_dir).startswith(str(disk["mount"]))

    def test_it_is_one_of_the_listed_disks(self, client):
        payload = client.get("/api/system/data").json()

        mounts = [entry["mount"] for entry in payload["disks"]]
        assert payload["disk"]["mount"] in mounts

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
            "cpu_count",
            "cpu_model",
            "gauntlet",
            "hostname",
            "kernel",
            "memory_total_bytes",
            "os",
            "python",
        }
        assert body["gauntlet"] == client.get("/api/version").json()["gauntlet"]
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
            "disks",
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


class TestCapabilities:
    def test_reads_a_mock(self, client) -> None:
        assert "channels" in client.get("/api/capabilities/psu").json()

    def test_writing_drives_a_command(self, client) -> None:
        body = client.post(
            "/api/capabilities/psu",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 9.0}},
        )
        assert body.status_code == 200
        assert body.json()["channels"]["1"]["voltage_setpoint"] == 9.0

    def test_the_snapshot_describes_every_provider(self, client) -> None:
        rows = client.get("/api/capabilities").json()["capabilities"]
        psu = next(row for row in rows if row["name"] == "psu")
        assert psu["available"] == "true"
        assert psu["instance_id"] == "psu0"
        assert psu["driver"] == "mock"

    def test_a_provider_that_cannot_be_read_is_405(self, client) -> None:
        client.app.state.capabilities.register(_Opaque())
        assert client.get("/api/capabilities/opaque").status_code == 405

    def test_a_provider_that_cannot_be_written_is_405(self, client) -> None:
        client.app.state.capabilities.register(_Opaque())
        assert client.post("/api/capabilities/opaque", json={}).status_code == 405

    def test_unknown_capability_is_404(self, client) -> None:
        assert client.get("/api/capabilities/nope").status_code == 404
        assert client.post("/api/capabilities/nope", json={}).status_code == 404


class _Opaque:
    """A provider with nothing but the four required members."""

    name = "opaque"

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"description": "write-only in the worst sense"}

    def instance_id(self) -> str:
        return "opaque0"

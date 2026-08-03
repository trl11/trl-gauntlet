"""Thermal zones, against a fake ``/sys`` tree."""

from __future__ import annotations

from pathlib import Path

from suite.thermal import read_thermal


def test_read_thermal_reads_every_readable_zone_in_zone_order(sys_tree: Path) -> None:
    zones = read_thermal(sys_root=sys_tree)

    assert [(zone.name, zone.label, zone.celsius) for zone in zones] == [
        ("thermal_zone0", "x86_pkg_temp", 42.5),
        ("thermal_zone10", "acpitz", 51.0),
    ]


def test_read_thermal_falls_back_to_the_zone_name_without_a_type(tmp_path: Path) -> None:
    zone = tmp_path / "class" / "thermal" / "thermal_zone0"
    zone.mkdir(parents=True)
    (zone / "temp").write_text("38000\n")

    zones = read_thermal(sys_root=tmp_path)

    assert [(zone.label, zone.celsius) for zone in zones] == [("thermal_zone0", 38.0)]


def test_read_thermal_returns_empty_without_the_directory(tmp_path: Path) -> None:
    assert read_thermal(sys_root=tmp_path / "absent") == ()

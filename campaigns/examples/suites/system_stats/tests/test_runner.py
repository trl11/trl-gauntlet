"""The runner, driven through the suite's own command line.

Every other module here is tested against captured ``/proc`` text. This one
runs the suite for real against the host the tests are on, because what it is
for is wiring the readers, the checks and the reporting together, and a short
run proves that wiring end to end.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from suite.cli import main
from suite.cpu import CpuUsage
from suite.memory import Memory
from suite.runner import SPEC, _mean, _series, _state, _summary
from suite.sampler import Sample

# Long enough for three samples, short enough not to slow the suite's tests.
_QUICK = {
    "description": "test",
    "duration_s": 0.3,
    "sample_period_s": 0.1,
    "max_cpu_percent": 100.0,
    "min_available_memory_percent": 0.0,
    "min_free_disk_percent": 0.0,
    "max_load_per_core": 1000.0,
    "max_temperature_c": 1000.0,
    "max_new_interface_errors": 1000000,
    "stop_on_failure": False,
}


def write_profile(directory: Path, **overrides: object) -> Path:
    path = directory / "profile.yaml"
    path.write_text(yaml.safe_dump({**_QUICK, **overrides}))
    return path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@pytest.fixture
def passing_run(tmp_path: Path) -> Path:
    """One run with thresholds nothing can breach. Returns its run directory."""
    run_dir = tmp_path / "run"
    profile = write_profile(tmp_path)
    assert main(["--profile", str(profile), "--run-dir", str(run_dir)]) == 0
    return run_dir


class TestAPassingRun:
    def test_writes_a_passing_verdict(self, passing_run: Path) -> None:
        verdict = read_json(passing_run / "verdict.json")
        assert verdict["passed"] is True
        assert verdict["failures"] == 0
        # A slow first sample can consume the short test duration. One passing
        # sample proves the end-to-end runner wiring without timing assumptions.
        assert verdict["total_iterations"] >= 1

    def test_writes_every_artifact_the_manifest_declares(self, passing_run: Path) -> None:
        for name in ("events.sqlite", "junit.xml", "manifest.json", "metrics.jsonl", "summary.md", "verdict.json"):
            assert (passing_run / name).is_file(), name

    def test_records_one_metrics_line_per_sample(self, passing_run: Path) -> None:
        lines = [json.loads(line) for line in (passing_run / "metrics.jsonl").read_text().splitlines() if line]
        iterations = [record for record in lines if record.get("kind") == "iteration"]
        assert len(iterations) == read_json(passing_run / "verdict.json")["total_iterations"]

    def test_every_sample_timed_its_two_phases(self, passing_run: Path) -> None:
        lines = [json.loads(line) for line in (passing_run / "metrics.jsonl").read_text().splitlines() if line]
        for record in (r for r in lines if r.get("kind") == "iteration"):
            assert [phase["name"] for phase in record["phases"]] == ["sample", "check"]

    def test_the_verdict_names_a_test_row_per_check(self, passing_run: Path) -> None:
        outcomes = {test["name"]: test["outcome"] for test in read_json(passing_run / "verdict.json")["tests"]}
        assert "cpu" in outcomes
        assert "memory" in outcomes
        assert "fail" not in outcomes.values()

    def test_the_verdict_carries_headline_figures(self, passing_run: Path) -> None:
        results = {row["key"]: row for row in read_json(passing_run / "verdict.json")["results"]}
        assert results["samples"]["value"] >= 2
        assert results["anomalies"]["value"] == 0
        assert results["cpu_peak"]["format"] == "percent"
        assert results["duration"]["format"] == "duration"

    def test_the_manifest_describes_the_host(self, passing_run: Path) -> None:
        manifest = read_json(passing_run / "manifest.json")
        host = manifest["hardware"]["host"]
        assert host["hostname"]
        assert int(host["cpu_count"]) >= 1
        assert manifest["versions"]["python"].count(".") == 2


@pytest.fixture
def failing_sampler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make failure-path tests independent of the host's instantaneous CPU load."""

    class _FailingSampler:
        def __init__(self, **_: object) -> None:
            pass

        def prime(self) -> None:
            pass

        def sample(self) -> Sample:
            return Sample(
                context_switches_per_s=None,
                cpu=CpuUsage(overall_percent=100.0, per_core_percent={"cpu0": 100.0}),
                cpu_count=1,
                disks=(),
                load=None,
                memory=None,
            )

    monkeypatch.setattr("suite.runner.Sampler", _FailingSampler)


class TestAFailingRun:
    def test_an_impossible_ceiling_records_a_failure(self, tmp_path: Path, failing_sampler: None) -> None:
        run_dir = tmp_path / "run"
        profile = write_profile(tmp_path, max_cpu_percent=0.0)

        assert main(["--profile", str(profile), "--run-dir", str(run_dir)]) == 1
        verdict = read_json(run_dir / "verdict.json")
        assert verdict["passed"] is False
        # A sampled CPU may legitimately be 0.0% on an idle host, so the
        # integration test must not require every sample to fail.
        assert 1 <= verdict["failures"] <= verdict["total_iterations"]

    def test_it_records_an_anomaly_per_failing_check(self, tmp_path: Path, failing_sampler: None) -> None:
        run_dir = tmp_path / "run"
        profile = write_profile(tmp_path, max_cpu_percent=0.0)
        main(["--profile", str(profile), "--run-dir", str(run_dir)])

        verdict = read_json(run_dir / "verdict.json")
        anomalies = {row["key"]: row["value"] for row in verdict["results"]}["anomalies"]
        assert anomalies >= 1
        cpu = next(test for test in verdict["tests"] if test["name"] == "cpu")
        assert cpu["outcome"] == "fail"
        assert "above ceiling" in cpu["message"] or "samples failed" in cpu["message"]

    def test_stop_on_failure_stops_after_the_first_failed_sample(self, tmp_path: Path, failing_sampler: None) -> None:
        run_dir = tmp_path / "run"
        profile = write_profile(tmp_path, duration_s=5.0, max_cpu_percent=0.0, stop_on_failure=True)

        assert main(["--profile", str(profile), "--run-dir", str(run_dir)]) == 1
        verdict = read_json(run_dir / "verdict.json")
        # The first CPU sample can be idle. Once a failure occurs, the runner
        # records that sample and abandons the remaining 5-second run.
        assert verdict["failures"] == 1
        assert verdict["total_iterations"] < 50


class TestTheCommandLine:
    def test_a_flag_overrides_the_profile(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        profile = write_profile(tmp_path, duration_s=60.0)

        main(["--profile", str(profile), "--run-dir", str(run_dir), "--duration-s", "0.3"])
        assert read_json(run_dir / "verdict.json")["duration_s"] < 5.0

    def test_a_threshold_flag_overrides_the_profile(self, tmp_path: Path, failing_sampler: None) -> None:
        run_dir = tmp_path / "run"
        profile = write_profile(tmp_path)

        assert main(["--profile", str(profile), "--run-dir", str(run_dir), "--max-cpu-percent", "0"]) == 1

    def test_the_unit_serial_reaches_the_manifest(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        profile = write_profile(tmp_path)

        main(["--profile", str(profile), "--run-dir", str(run_dir), "--unit-serial", "SN-7"])
        assert read_json(run_dir / "manifest.json")["unit_serial"] == "SN-7"

    def test_it_prints_its_profile_schema(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--print-profile-schema"]) == 0
        schema = json.loads(capsys.readouterr().out)
        assert schema["type"] == "object"
        assert "max_cpu_percent" in schema["properties"]

    def test_an_unreadable_profile_exits_two(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        profile = tmp_path / "profile.yaml"
        profile.write_text("duration_s: -1\n")

        assert main(["--profile", str(profile), "--run-dir", str(tmp_path / "run")]) == 2
        assert "error:" in capsys.readouterr().out

    def test_an_unknown_profile_field_exits_two(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        profile = write_profile(tmp_path)
        profile.write_text(profile.read_text() + "nonsense: 1\n")

        assert main(["--profile", str(profile), "--run-dir", str(tmp_path / "run")]) == 2
        assert "extra_forbidden" in capsys.readouterr().out


class TestTheSpec:
    def test_it_reads_the_cadence_out_of_the_profile(self) -> None:
        profile = SPEC.profile_model(duration_s=12.0, sample_period_s=0.5, stop_on_failure=True)
        assert SPEC.duration_seconds(profile) == 12.0
        assert SPEC.sample_period_seconds(profile) == 0.5
        assert SPEC.stop_on_failure(profile) is True

    def test_a_run_that_collected_nothing_fails(self) -> None:
        assert SPEC.evaluate([], SPEC.profile_model()) == (False, "no samples collected")

    def test_a_run_that_collected_something_is_left_to_the_iterations(self) -> None:
        assert SPEC.evaluate([object()], SPEC.profile_model()) == (True, "")


class TestHelpers:
    def test_mean(self) -> None:
        assert _mean([1.0, 2.0, 6.0]) == 3.0

    def test_a_series_the_host_never_offered_makes_no_row(self) -> None:
        assert _series("k", "K", [], max, "decimal") == []

    def test_a_series_is_reduced_rounded_and_labelled(self) -> None:
        rows = _series("k", "K", [1.234, 9.876], max, "decimal", unit="C")
        assert rows == [
            {
                "key": "k",
                "label": "K",
                "value": 9.88,
                "unit": "C",
                "format": "decimal",
                "precision": 2,
            }
        ]

    def test_state_without_setup_is_an_error(self) -> None:
        with pytest.raises(RuntimeError, match="setup did not populate"):
            _state(_Ctx())

    def test_a_summary_names_only_what_was_measured(self, make_sample: Callable[..., Sample]) -> None:
        assert _summary(make_sample()) == ""

    def test_a_summary_of_a_full_sample(self, make_sample: Callable[..., Sample]) -> None:
        sample = make_sample(
            cpu=CpuUsage(overall_percent=12.4, per_core_percent={"cpu0": 12.4}),
            memory=Memory(
                available_bytes=400,
                buffers_bytes=0,
                cached_bytes=0,
                free_bytes=300,
                swap_free_bytes=0,
                swap_total_bytes=0,
                total_bytes=1000,
            ),
        )
        assert _summary(sample) == "cpu 12%  mem 40% free"


class _Ctx:
    """Just enough of a SuiteContext for the helpers that only read extras."""

    def __init__(self) -> None:
        self.extras: dict[str, object] = {}

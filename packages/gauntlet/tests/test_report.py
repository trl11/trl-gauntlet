"""A run as one self-contained HTML page."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from gauntlet.report import render_report
from gauntlet.storage import NoteRow, RunRow


def make_row(run_dir: Path, **fields) -> RunRow:
    values = {
        "run_id": "r1",
        "suite": "alpha",
        "status": "failed",
        "started_at": "2026-01-01T00:00:00Z",
        "run_dir": str(run_dir),
        "duration_s": 75.0,
        "unit_serial": "SN1",
    }
    values.update(fields)
    return RunRow(**values)


def write_metrics(run_dir: Path, records: list[dict]) -> None:
    (run_dir / "metrics.jsonl").write_text("".join(json.dumps(record) + "\n" for record in records))


class TestRenderReport:
    def test_names_the_run(self, tmp_path: Path) -> None:
        page = render_report(make_row(tmp_path), [], "Radiation TID")

        assert "<title>alpha · r1</title>" in page
        assert "FAILED" in page
        assert "SN1" in page
        assert "Radiation TID" in page
        assert "1m 15s" in page

    def test_fetches_nothing(self, tmp_path: Path) -> None:
        page = render_report(make_row(tmp_path), [], None)

        assert "<script" not in page
        assert "<link" not in page
        assert "src=" not in page

    def test_carries_the_verdict(self, tmp_path: Path) -> None:
        verdict = {
            "passed": False,
            "reason": "rail low on cycle 7",
            "results": [{"key": "vmin", "label": "Minimum rail", "value": 3.1, "unit": "V"}],
            "tests": [{"name": "rail", "outcome": "failed", "message": "3.1 < 3.2"}],
        }
        (tmp_path / "verdict.json").write_text(json.dumps(verdict))

        page = render_report(make_row(tmp_path), [], None)

        assert "rail low on cycle 7" in page
        assert "Minimum rail" in page
        assert "3.1 V" in page
        assert "3.1 &lt; 3.2" in page

    def test_summarises_and_charts_each_numeric_metric(self, tmp_path: Path) -> None:
        write_metrics(
            tmp_path,
            [
                {"iteration": 1, "elapsed_run_s": 0.0, "success": True, "metrics": {"rail": {"v": 3.3}, "ok": True}},
                {
                    "iteration": 2,
                    "elapsed_run_s": 1.0,
                    "success": False,
                    "reason": "rail low",
                    "metrics": {"rail": {"v": 3.1}},
                },
                {"kind": "anomaly", "probe": "i2c"},
            ],
        )

        page = render_report(make_row(tmp_path), [], None)

        assert "rail.v" in page
        assert "<polyline" in page
        assert "rail low" in page
        assert ">ok<" not in page

    def test_a_series_that_never_changed_is_not_charted(self, tmp_path: Path) -> None:
        write_metrics(
            tmp_path,
            [{"iteration": i, "elapsed_run_s": i, "success": True, "metrics": {"flat": 1}} for i in range(3)],
        )

        page = render_report(make_row(tmp_path), [], None)

        assert "<td>flat</td>" in page
        assert "<polyline" not in page

    def test_a_long_series_is_thinned_for_its_chart(self, tmp_path: Path) -> None:
        write_metrics(
            tmp_path,
            [{"iteration": i, "elapsed_run_s": i, "success": True, "metrics": {"x": i}} for i in range(5000)],
        )

        page = render_report(make_row(tmp_path), [], None)

        points = page.split('points="')[1].split('"')[0].split()
        assert len(points) <= 500
        assert "<td>5000</td>" in page

    def test_carries_the_instruments_the_run_recorded(self, tmp_path: Path) -> None:
        summary = {
            "instruments": [
                {
                    "name": "psu",
                    "description": "HM310T",
                    "readings": [
                        {"key": "v", "label": "Output", "unit": "V", "min": 5, "max": 5.1, "mean": 5.05, "last": 5}
                    ],
                }
            ]
        }
        (tmp_path / "instruments.json").write_text(json.dumps(summary))

        page = render_report(make_row(tmp_path), [], None)

        assert "psu — HM310T" in page
        assert "Output" in page
        assert "<th>Group</th>" not in page

    def test_tells_the_channels_of_one_instrument_apart(self, tmp_path: Path) -> None:
        readings = [
            {
                "key": f"channels.{n}.voltage",
                "label": "Voltage",
                "group": f"Channel {n}",
                "min": 5,
                "max": 5,
                "mean": 5,
                "last": 5,
            }
            for n in (1, 2)
        ]
        (tmp_path / "instruments.json").write_text(json.dumps({"instruments": [{"name": "psu", "readings": readings}]}))

        page = render_report(make_row(tmp_path), [], None)

        assert "<th>Group</th>" in page
        assert "<td>Channel 1</td><td>Voltage</td>" in page
        assert "<td>Channel 2</td><td>Voltage</td>" in page

    def test_carries_the_notes_escaped(self, tmp_path: Path) -> None:
        note = NoteRow(
            id=1,
            subject_kind="run",
            subject_id="r1",
            body="<b>swapped</b> the cable",
            created_at="2026-01-01",
            author="gabe",
        )

        page = render_report(make_row(tmp_path), [note], None)

        assert "&lt;b&gt;swapped&lt;/b&gt; the cable" in page
        assert "gabe" in page

    def test_lists_the_profile_one_setting_a_row(self, tmp_path: Path) -> None:
        (tmp_path / "profile.yaml").write_text(
            "duration_s: 0.0\n"
            "dmesg:\n  enabled: true\n  patterns: [nvme, I/O error]\n"
            "probe:\n  devices:\n  - name: nvme0\n    device: /dev/nvme0n1\n"
            "ssh_key_path: ''\n"
        )

        page = render_report(make_row(tmp_path), [], None)

        assert "<td>dmesg.enabled</td><td>yes</td>" in page
        assert "<td>dmesg.patterns</td><td>nvme, I/O error</td>" in page
        assert "<td>probe.devices[0].device</td><td>/dev/nvme0n1</td>" in page
        assert "<td>ssh_key_path</td><td>-</td>" in page

    def test_takes_the_profile_from_the_manifest_when_the_file_is_missing(self, tmp_path: Path) -> None:
        manifest = {"profile": {"sample_period_s": 30.0}, "profile_path": "/suites/x/profiles/soak.yaml"}
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))

        page = render_report(make_row(tmp_path), [], None)

        assert "<td>sample_period_s</td><td>30.0</td>" in page
        assert "/suites/x/profiles/soak.yaml" in page

    def test_shows_no_file_raw(self, tmp_path: Path) -> None:
        (tmp_path / "summary.md").write_text("# alpha — PASS\n")
        (tmp_path / "profile.yaml").write_text("duration_s: 5\n")

        page = render_report(make_row(tmp_path), [], None)

        assert "# alpha" not in page
        assert "<pre>" not in page

    def test_a_run_whose_directory_is_gone_still_reports(self, tmp_path: Path) -> None:
        page = render_report(make_row(tmp_path / "gone"), [], None)

        assert "alpha" in page
        assert "Artifacts" not in page

    def test_an_unreadable_file_leaves_its_section_out(self, tmp_path: Path) -> None:
        (tmp_path / "verdict.json").write_text("{not json")
        (tmp_path / "metrics.jsonl").write_text("garbage\n")

        page = render_report(make_row(tmp_path), [], None)

        assert "<h2>Verdict</h2>" not in page
        assert "<h2>Metrics</h2>" not in page


class TestReportEndpoint:
    def test_offers_the_page_as_a_download(self, client, add_run, make_run_dir) -> None:
        add_run("r1", run_dir=make_run_dir())

        response = client.get("/api/runs/r1/report")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "r1.report.html" in response.headers["content-disposition"]
        assert "frames/0001.png" in response.text

    def test_carries_the_notes(self, client, add_run, make_run_dir) -> None:
        add_run("r1", run_dir=make_run_dir())
        client.post("/api/runs/r1/notes", json={"body": "swapped the cable"})

        assert "swapped the cable" in client.get("/api/runs/r1/report").text

    def test_an_unknown_run_is_404(self, client) -> None:
        assert client.get("/api/runs/nope/report").status_code == 404

    def test_a_run_still_in_flight_is_409(self, client, add_run, make_run_dir, monkeypatch) -> None:
        add_run("r1", run_dir=make_run_dir())
        monkeypatch.setattr(client.app.state.supervisor, "get", lambda _: SimpleNamespace(finished=False))

        assert client.get("/api/runs/r1/report").status_code == 409

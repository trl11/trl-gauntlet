"""The verifier itself — it has to fail suites that deserve to fail."""

from __future__ import annotations

import textwrap

from gauntlet.conformance import verify_suite


def _check(report, name):
    return next(c for c in report.checks if c.name == name)


class TestStaticChecks:
    def test_conforming_suite_passes(self, make_suite):
        report = verify_suite(make_suite("alpha").directory)
        assert report.passed, [c.name for c in report.checks if not c.passed]

    def test_missing_manifest_is_fatal(self, tmp_path):
        report = verify_suite(tmp_path)
        assert not report.passed
        assert report.checks[0].fatal

    def test_relative_command_resolves(self, make_suite):
        report = verify_suite(make_suite("alpha").directory)
        assert _check(report, "exec.command resolves").passed

    def test_missing_verdict_in_produces_fails(self, make_suite):
        suite = make_suite("alpha", produces=["metrics"])
        report = verify_suite(suite.directory)
        assert not _check(report, "produces includes verdict").passed

    def test_missing_conformance_profile_is_flagged(self, make_suite):
        suite = make_suite("alpha", conformance_profile="")
        report = verify_suite(suite.directory)
        assert not _check(report, "conformance_profile is declared").passed


class TestExecutionChecks:
    def test_conforming_suite_passes_a_real_run(self, make_suite):
        report = verify_suite(make_suite("alpha").directory, execute=True)
        assert report.executed
        assert report.passed, [c.name for c in report.checks if not c.passed]

    def test_suite_that_writes_no_verdict_fails(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            echo "doing nothing"
            exit 0
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)
        assert not report.passed
        assert not _check(report, "run: verdict.json written").passed

    def test_malformed_verdict_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{"passed": "yes please"}' > "$run_dir/verdict.json"
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)
        assert not _check(report, "run: verdict.json matches the contract").passed

    def test_failing_verdict_without_a_reason_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{"passed": false, "reason": ""}' > "$run_dir/verdict.json"
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)
        assert not _check(report, "run: verdict.json is self-consistent").passed

    def test_declared_but_unwritten_artifact_is_caught(self, make_suite):
        suite = make_suite("alpha", produces=["metrics", "verdict", "junit"])
        report = verify_suite(suite.directory, execute=True)
        assert not _check(report, "run: declared artifact junit written").passed

    def test_malformed_metrics_line_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{"kind":"iteration","timestamp":1}' > "$run_dir/metrics.jsonl"
            echo '{"passed": true}' > "$run_dir/verdict.json"
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)
        check = _check(report, "run: metrics.jsonl records match the contract")
        assert not check.passed
        assert "iteration" in check.detail

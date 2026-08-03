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

    def test_a_conformance_profile_that_does_not_exist_stops_the_run(self, make_suite):
        suite = make_suite("alpha", conformance_profile="missing.yaml")
        report = verify_suite(suite.directory, execute=True)

        assert not report.executed
        assert not _check(report, "run: conformance profile resolved").passed

    def test_a_suite_that_never_finishes_times_out(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            for _ in $(seq 1 600); do sleep 0.1; done
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True, timeout_s=0.5)

        check = _check(report, "run: suite completes")
        assert not check.passed
        assert check.fatal
        assert "timed out" in check.detail

    def test_a_command_that_cannot_be_spawned_is_reported(self, make_suite):
        suite = make_suite("alpha")
        (suite.directory / "run.sh").chmod(0o644)

        report = verify_suite(suite.directory, execute=True)

        assert not _check(report, "run: suite starts").passed

    def test_a_crash_reports_the_exit_code_and_the_tail_of_its_output(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            for i in $(seq 1 30); do echo "line $i"; done
            exit 9
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)

        check = _check(report, "run: suite exits cleanly")
        assert not check.passed
        assert "exit code 9" in check.detail
        assert "line 30" in check.detail
        assert "line 16" in check.detail
        assert "line 15" not in check.detail

    def test_a_failing_verdict_is_still_a_clean_exit(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{"passed": false, "reason": "too hot"}' > "$run_dir/verdict.json"
            exit 1
            """
        )
        report = verify_suite(make_suite("alpha", produces=["verdict"], script=script).directory, execute=True)

        assert _check(report, "run: suite exits cleanly").passed

    def test_a_verdict_that_is_not_json_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{ truncated' > "$run_dir/verdict.json"
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)

        assert not _check(report, "run: verdict.json is valid JSON").passed

    def test_an_artifact_written_without_being_declared_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{"passed": true}' > "$run_dir/verdict.json"
            echo '<testsuite/>' > "$run_dir/junit.xml"
            """
        )
        report = verify_suite(make_suite("alpha", produces=["verdict"], script=script).directory, execute=True)

        check = _check(report, "run: no undeclared artifacts")
        assert not check.passed
        assert "junit" in check.detail

    def test_declared_frames_must_be_a_non_empty_directory(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir/frames"
            echo '{"passed": true}' > "$run_dir/verdict.json"
            """
        )
        suite = make_suite("alpha", produces=["verdict", "frames"], script=script)

        assert not _check(verify_suite(suite.directory, execute=True), "run: declared artifact frames written").passed

    def test_frames_written_into_the_directory_satisfy_the_check(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir/frames"
            echo 'not really a png' > "$run_dir/frames/shot.png"
            echo '{"passed": true}' > "$run_dir/verdict.json"
            """
        )
        suite = make_suite("alpha", produces=["verdict", "frames"], script=script)

        assert _check(verify_suite(suite.directory, execute=True), "run: declared artifact frames written").passed

    def test_a_metrics_line_that_is_not_json_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            printf '{ truncated\\n\\n{"kind":"live","timestamp":1}\\n' > "$run_dir/metrics.jsonl"
            echo '{"passed": true}' > "$run_dir/verdict.json"
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)

        check = _check(report, "run: metrics.jsonl records match the contract")
        assert not check.passed
        assert "line 1: not JSON" in check.detail

    def test_a_metrics_line_that_does_not_match_the_model_is_caught(self, make_suite):
        script = textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            run_dir="${GAUNTLET_RUN_DIR}"
            mkdir -p "$run_dir"
            echo '{"kind":"iteration","timestamp":"noon","iteration":1,"success":true}' > "$run_dir/metrics.jsonl"
            echo '{"passed": true}' > "$run_dir/verdict.json"
            """
        )
        report = verify_suite(make_suite("alpha", script=script).directory, execute=True)

        assert not _check(report, "run: metrics.jsonl records match the contract").passed

    def test_a_clean_metrics_file_reports_how_many_records_were_checked(self, make_suite):
        report = verify_suite(make_suite("alpha").directory, execute=True)

        check = _check(report, "run: metrics.jsonl records match the contract")
        assert check.passed
        assert "1 records checked" in check.detail

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

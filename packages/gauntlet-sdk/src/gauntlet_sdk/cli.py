"""Builds a suite's command-line entry point from its spec.

The flags here are the ones ``suite.yaml`` names in ``exec.args``, so a suite
built with :func:`make_suite_cli` works under Gauntlet and by hand from a
terminal without a second code path.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from gauntlet_sdk.environment import run_environment
from gauntlet_sdk.log import err, info
from gauntlet_sdk.profile import ProfileError, load_profile
from gauntlet_sdk.runner import SuiteSpec, run_suite


class SuiteMain(Protocol):
    """The entry point :func:`make_suite_cli` returns.

    ``argv`` defaults to the process arguments, so a console script calls it
    with nothing and a test passes a list.
    """

    def __call__(self, argv: Sequence[str] | None = None) -> int: ...


def make_suite_cli(
    spec: SuiteSpec,
    *,
    prog: str | None = None,
    description: str | None = None,
    default_profile: Path | None = None,
    extra_args: Callable[[argparse.ArgumentParser], None] | None = None,
    extra_overrides: Callable[[argparse.Namespace], dict[str, Any]] | None = None,
) -> SuiteMain:
    """Return a ``main`` for this suite.

    ``extra_args`` adds suite-specific flags to the parser and
    ``extra_overrides`` turns the parsed values into profile overrides. Any
    flag added this way must also appear in the suite's ``overrides:`` list in
    ``suite.yaml`` for Gauntlet to offer it.
    """

    def main(argv: Sequence[str] | None = None) -> int:
        parser = argparse.ArgumentParser(prog=prog or spec.name, description=description or f"{spec.name} suite")
        parser.add_argument(
            "--print-profile-schema",
            action="store_true",
            help="print this suite's profile as JSON Schema and exit",
        )
        parser.add_argument("--profile", type=Path, default=default_profile, help="profile YAML to run")
        parser.add_argument("--run-dir", type=Path, default=None, help="directory to write artifacts into")
        parser.add_argument("--target", default=None, help="address of the unit under test")
        parser.add_argument("--unit-serial", default=None, help="serial number of the unit under test")
        parser.add_argument("--duration-s", type=float, default=None, help="override the run duration")
        parser.add_argument("--sample-period-s", type=float, default=None, help="override the sample cadence")
        parser.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="override an arbitrary profile field (repeatable)",
        )
        if extra_args is not None:
            extra_args(parser)
        args = parser.parse_args(argv)

        if args.print_profile_schema:
            # Gauntlet calls this to render a profile editor form, which is why
            # it prints to stdout and nothing else does.
            print(json.dumps(spec.profile_model.model_json_schema(), indent=2))
            return 0

        overrides: dict[str, Any] = {}
        if args.duration_s is not None:
            overrides["duration_s"] = args.duration_s
        if args.sample_period_s is not None:
            overrides["sample_period_s"] = args.sample_period_s
        for item in args.set:
            key, _, raw = str(item).partition("=")
            if not key or not _:
                err(f"--set expects KEY=VALUE, got {item!r}")
                return 2
            overrides[key.strip()] = _coerce(raw)
        if extra_overrides is not None:
            overrides.update({k: v for k, v in extra_overrides(args).items() if v is not None})

        env = run_environment(
            run_dir=args.run_dir,
            profile_path=args.profile,
            target=args.target,
            unit_serial=args.unit_serial,
            suite=spec.name,
        )
        try:
            profile = load_profile(spec.profile_model, env.profile_path, overrides=overrides)
        except ProfileError as exc:
            err(str(exc))
            return 2

        info(f"{spec.name}: run_dir={env.run_dir}")
        result, run_dir = run_suite(spec, profile, env=env)
        info(f"artifacts: {run_dir}")
        return 0 if result.passed else 1

    return main


def _coerce(raw: str) -> Any:
    """Turn a ``--set`` string into the obvious Python scalar."""
    text = raw.strip()
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"none", "null"}:
        return None
    for caster in (int, float):
        try:
            return caster(text)
        except ValueError:
            continue
    return text

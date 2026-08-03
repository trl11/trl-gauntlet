"""The ``gauntlet`` command."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from gauntlet_sdk.contract import CONTRACT_MODELS, json_schema

from gauntlet.config import Settings, load_settings
from gauntlet.conformance import Report, verify_suite
from gauntlet.scaffold import ScaffoldError, available_templates, render
from gauntlet.suites import discover_suites


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="gauntlet", description="Run test suites that conform to the contract.")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the web UI and API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--suites", action="append", default=None, metavar="DIR", help="suite root (repeatable)")
    serve.add_argument("--reload", action="store_true", help="restart on code changes")
    serve.add_argument("--log-level", default=None, choices=["debug", "info", "warning", "error"])

    listing = sub.add_parser("list", help="list discovered suites")
    listing.add_argument("--suites", action="append", default=None, metavar="DIR")
    listing.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="check a suite against the contract")
    verify.add_argument("directory", type=Path, nargs="?", help="suite directory (default: every discovered suite)")
    verify.add_argument("--run", action="store_true", help="execute the conformance profile and check its artifacts")
    verify.add_argument("--suites", action="append", default=None, metavar="DIR")
    verify.add_argument("--json", action="store_true")

    schema = sub.add_parser("schema", help="print a contract schema as JSON Schema")
    schema.add_argument("name", nargs="?", choices=sorted(CONTRACT_MODELS), help="omit to list the names")

    scaffold = sub.add_parser("new-suite", help="generate a suite from a template")
    scaffold.add_argument("name", help="suite key in lower_snake_case")
    scaffold.add_argument(
        "--template",
        default="python",
        choices=available_templates(),
        help="template to render (default: python)",
    )
    scaffold.add_argument("--into", type=Path, default=None, metavar="DIR", help="where to create the suite")

    sub.add_parser("templates", help="list the suite templates new-suite can render")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=(getattr(args, "log_level", None) and args.log_level.upper()) or "INFO",
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "serve":
        return _serve(args)
    if args.command == "list":
        return _list(args)
    if args.command == "verify":
        return _verify(args)
    if args.command == "schema":
        return _schema(args)
    if args.command == "new-suite":
        return _new_suite(args)
    if args.command == "templates":
        print("\n".join(available_templates()))
        return 0
    return 2


def _settings(args: argparse.Namespace) -> Settings:
    overrides = {}
    for key in ("host", "port", "log_level"):
        value = getattr(args, key, None)
        if value is not None:
            overrides[key] = value
    if getattr(args, "suites", None):
        overrides["suite_roots"] = [Path(p) for p in args.suites]
    return load_settings(overrides)


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = _settings(args)
    settings.ensure_dirs()
    print(f"Gauntlet on http://{settings.host}:{settings.port}", file=sys.stderr)
    print(f"  suites  {', '.join(str(p) for p in settings.suite_roots)}", file=sys.stderr)
    print(f"  runs    {settings.runs_dir}", file=sys.stderr)
    if args.reload:
        # Reload requires an import string rather than an application instance.
        uvicorn.run(
            "gauntlet.app:create_app",
            factory=True,
            host=settings.host,
            port=settings.port,
            reload=True,
            log_level=settings.log_level,
        )
    else:
        from gauntlet.app import create_app

        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level)
    return 0


def _list(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = discover_suites(settings.suite_roots)
    if args.json:
        print(json.dumps(catalog.to_dict(), indent=2))
        return 0 if not catalog.errors else 1
    if not catalog.suites:
        print(f"No suites found in {', '.join(str(p) for p in settings.suite_roots)}")
    width = max((len(k) for k in catalog.suites), default=0)
    for key in sorted(catalog.suites):
        suite = catalog.suites[key]
        print(f"  {key.ljust(width)}  {suite.manifest.category:<14} {suite.manifest.title}")
    for error in catalog.errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if catalog.errors else 0


def _verify(args: argparse.Namespace) -> int:
    if args.directory is not None:
        reports = [verify_suite(args.directory, execute=args.run)]
    else:
        settings = _settings(args)
        catalog = discover_suites(settings.suite_roots)
        reports = [verify_suite(s.directory, execute=args.run) for s in catalog.suites.values()]
        if not reports:
            print("no suites to verify", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        for report in reports:
            _print_report(report)
    return 0 if all(r.passed for r in reports) else 1


def _print_report(report: Report) -> None:
    status = "PASS" if report.passed else "FAIL"
    print(f"\n{report.suite}  [{status}]  {report.directory}")
    for check in report.checks:
        mark = "ok  " if check.passed else "FAIL"
        print(f"  {mark} {check.name}")
        if check.detail:
            for line in check.detail.splitlines():
                print(f"         {line}")


def _new_suite(args: argparse.Namespace) -> int:
    destination_root = args.into or (Path.cwd() / "suites")
    try:
        destination = render(args.name, destination_root, template=args.template)
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    where = _relative_to_cwd(destination)
    entry = "run.sh" if args.template == "shell" else "suite/runner.py"
    print(f"Created {where}  (template: {args.template})")
    print()
    print("Next:")
    print(f"  1. edit {where}/{entry}")
    print("  2. gauntlet verify --run")
    print("  3. gauntlet serve")
    return 0


def _relative_to_cwd(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _schema(args: argparse.Namespace) -> int:
    if not args.name:
        print("\n".join(sorted(CONTRACT_MODELS)))
        return 0
    print(json.dumps(json_schema(args.name), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

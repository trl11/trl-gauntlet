"""Profile loading and override application.

A profile is a YAML file parameterizing one run. Suites define the shape with
a pydantic model; setting ``extra="forbid"`` on that model means a typo in a
profile fails at load with a clear message instead of being silently ignored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

P = TypeVar("P", bound=BaseModel)


class ProfileError(ValueError):
    """A profile could not be read or did not match the suite's model."""


def load_profile(model: type[P], path: Path | None, *, overrides: dict[str, Any] | None = None) -> P:
    """Load, validate, and return a profile.

    With no path, the model's own defaults are used, which is what makes
    ``--profile`` optional for suites whose defaults are already sensible.
    """
    raw: dict[str, Any] = {}
    if path is not None:
        try:
            loaded = yaml.safe_load(path.read_text())
        except OSError as exc:
            raise ProfileError(f"cannot read profile {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ProfileError(f"invalid YAML in {path}: {exc}") from exc
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ProfileError(f"profile {path} must be a mapping, got {type(loaded).__name__}")
        raw = loaded

    if overrides:
        raw = {**raw, **{k: v for k, v in overrides.items() if v is not None}}

    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        where = str(path) if path else "<defaults>"
        raise ProfileError(f"profile {where} does not match {model.__name__}:\n{exc}") from exc


def write_resolved_profile(profile: BaseModel, run_dir: Path) -> Path | None:
    """Write the profile a run actually used, as YAML, over the copied file.

    The copy taken at the start is the file as handed over: it carries whatever
    the operator wrote and nothing the profile model filled in, and none of the
    overrides the run was started with, which reach the suite as flags. What
    ran is this — every field, defaults included, overrides folded in — and it
    is what somebody reproducing the run needs.

    Written at the end, because the copy is what a run that dies before
    resolving anything leaves behind, and that is better than no profile at all.
    """
    try:
        text = yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=True, default_flow_style=False)
    except (TypeError, ValueError, yaml.YAMLError):
        return None
    dest = run_dir / "profile.yaml"
    try:
        dest.write_text(text, encoding="utf-8")
    except OSError:
        return None
    return dest


def summarize_profile(profile: BaseModel, *, fields: list[str] | None = None) -> dict[str, str]:
    """Flatten selected profile fields into strings for the run manifest.

    With no field list, every scalar top-level field is included.
    """
    data = profile.model_dump()
    if fields is not None:
        return {name: str(data[name]) for name in fields if name in data}
    return {k: str(v) for k, v in data.items() if isinstance(v, (bool, int, float, str))}


def snapshot_profile(source: Path | None, run_dir: Path) -> Path | None:
    """Copy the profile into the run directory so the run stays reproducible.

    Gauntlet does this for supervised runs; suites call it so a standalone run
    gets the same record. It is the file as handed over, written before the run
    starts, and :func:`write_resolved_profile` replaces it at the end with what
    the run made of it.
    """
    if source is None or not source.is_file():
        return None
    dest = run_dir / "profile.yaml"
    if dest.exists():
        return dest
    try:
        dest.write_bytes(source.read_bytes())
    except OSError:
        return None
    return dest

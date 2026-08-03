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
    gets the same record.
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

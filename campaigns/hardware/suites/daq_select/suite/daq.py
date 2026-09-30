"""The acquisition units, as this suite reaches them.

Gauntlet owns the devices. This suite is not granted them through
``requires:``, because which units a run measures is the operator's choice at
run time, so it asks the running Gauntlet what the bench holds and drives each
unit it picks at the same URL a grant would have carried.

``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class DaqError(RuntimeError):
    """The instrument refused a command, or could not be reached."""


@dataclass(frozen=True)
class Unit:
    """One acquisition unit a run measures."""

    key: str
    instance: str
    url: str


def find_units(api_base: str | None, selected: list[str], *, timeout_s: float = 15.0) -> list[Unit]:
    """The units to measure: the ones named, or every DAQ on the bench when none is.

    A name that is not registered, or whose unit is not answering, fails here
    rather than part way through a run.
    """
    if not api_base:
        raise DaqError("no Gauntlet API to ask which DAQs the bench holds")
    base = api_base.rstrip("/")
    rows = [row for row in _get(f"{base}/instruments", timeout_s)["instruments"] if row.get("kind") == "daq"]
    by_key = {row["name"]: row for row in rows}
    if not selected:
        selected = sorted(by_key)
    if not selected:
        raise DaqError("this bench has no DAQ registered")
    missing = [key for key in selected if key not in by_key]
    if missing:
        raise DaqError(f"no DAQ named {', '.join(missing)} (registered: {', '.join(sorted(by_key)) or 'none'})")
    silent = [key for key in selected if not by_key[key].get("available")]
    if silent:
        raise DaqError(
            f"{', '.join(silent)} not answering: {by_key[silent[0]].get('unavailable_reason') or 'unavailable'}"
        )
    return [Unit(key, by_key[key]["instance_id"], f"{base}/capabilities/{key}") for key in selected]


def configure(unit: Unit, rows: dict[str, dict[str, str]], *, timeout_s: float = 15.0) -> dict[str, Any]:
    """Set the mode and label of every channel named, in one exchange.

    A channel no row names is left as it is, so a bench carrying more than this
    run measures keeps the rest of its settings.
    """
    return _post(unit.url, {"command": "configure", "args": {"rows": rows}}, timeout_s)["channels"]


def sample(unit: Unit, *, timeout_s: float = 15.0) -> dict[str, Any]:
    """Take one scan and return every channel as the instrument reports it.

    A scan rather than a read of the last one: reading would answer from
    whatever the panel last refreshed, which at a slow cadence is a value older
    than the sample it is being recorded as.
    """
    return _post(unit.url, {"command": "sample", "args": {}}, timeout_s)["channels"]


def _get(url: str, timeout_s: float) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as reply:
            return dict(json.load(reply))
    except urllib.error.HTTPError as exc:
        raise DaqError(f"{url}: {_detail(exc)}") from exc
    except (OSError, ValueError) as exc:
        raise DaqError(f"{url}: {exc}") from exc


def _post(url: str, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as reply:
            return dict(json.load(reply))
    except urllib.error.HTTPError as exc:
        raise DaqError(f"{body['command']}: {_detail(exc)}") from exc
    except (OSError, ValueError) as exc:
        raise DaqError(f"{body['command']}: {exc}") from exc


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for an error it explained.

    A rejected command answers 422 carrying the provider's own words, which is
    the difference between "mode must be one of ..." and "HTTP 422".
    """
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"

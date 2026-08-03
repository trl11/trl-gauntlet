"""The capability registry: what it grants a run, and what it refuses."""

from __future__ import annotations

import pytest

from gauntlet.capabilities import CapabilityError, CapabilityRegistry, Grant, MockPsu

_BASE = "http://127.0.0.1:7100/api"


class _Provider:
    """A provider whose availability the test sets."""

    def __init__(self, name: str, *, available: bool = True) -> None:
        self.name = name
        self._available = available

    def available(self) -> bool:
        return self._available

    def describe(self) -> dict[str, str]:
        return {"description": f"the {self.name}", "driver": "test"}

    def instance_id(self) -> str:
        return f"{self.name}0"


class TestGrant:
    def test_flattens_into_environment_variables(self) -> None:
        grant = Grant(name="psu", instance_id="psu0", url=f"{_BASE}/capabilities/psu")
        assert grant.as_env() == {
            "GAUNTLET_CAP_PSU_URL": f"{_BASE}/capabilities/psu",
            "GAUNTLET_CAP_PSU_ID": "psu0",
        }


class TestRegistry:
    def test_names_are_sorted(self) -> None:
        registry = CapabilityRegistry()
        for name in ("psu", "chamber", "daq"):
            registry.register(_Provider(name))
        assert registry.names() == ["chamber", "daq", "psu"]

    def test_registering_the_same_name_twice_replaces_it(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("psu"))
        registry.register(MockPsu())
        assert isinstance(registry.provider("psu"), MockPsu)

    def test_an_unregistered_name_is_none(self) -> None:
        assert CapabilityRegistry().provider("psu") is None

    def test_missing_reports_the_unregistered_and_the_unavailable(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("psu"))
        registry.register(_Provider("chamber", available=False))
        assert registry.missing(["psu", "chamber", "scope"]) == ["chamber", "scope"]

    def test_nothing_required_needs_nothing(self) -> None:
        assert CapabilityRegistry().grants([]) == []
        assert CapabilityRegistry().environment([]) == {}

    def test_a_grant_addresses_the_provider_through_the_api(self) -> None:
        registry = CapabilityRegistry(api_base=f"{_BASE}/")
        registry.register(_Provider("psu"))
        assert registry.grants(["psu"]) == [Grant(name="psu", instance_id="psu0", url=f"{_BASE}/capabilities/psu")]

    def test_environment_flattens_every_grant(self) -> None:
        registry = CapabilityRegistry(api_base=_BASE)
        registry.register(_Provider("psu"))
        registry.register(_Provider("daq"))
        assert set(registry.environment(["psu", "daq"])) == {
            "GAUNTLET_CAP_PSU_URL",
            "GAUNTLET_CAP_PSU_ID",
            "GAUNTLET_CAP_DAQ_URL",
            "GAUNTLET_CAP_DAQ_ID",
        }

    def test_an_unmet_requirement_names_what_is_registered(self) -> None:
        registry = CapabilityRegistry(api_base=_BASE)
        registry.register(_Provider("psu"))
        with pytest.raises(CapabilityError) as caught:
            registry.grants(["scope"])
        assert "scope unavailable" in str(caught.value)
        assert "registered: psu" in str(caught.value)

    def test_an_empty_registry_says_so(self) -> None:
        with pytest.raises(CapabilityError, match="registered: none"):
            CapabilityRegistry(api_base=_BASE).grants(["psu"])

    def test_a_registry_without_an_api_base_cannot_grant(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("psu"))
        with pytest.raises(CapabilityError, match="API base URL is unknown"):
            registry.grants(["psu"])

    def test_the_snapshot_merges_the_provider_description(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("psu", available=False))
        assert registry.snapshot() == [
            {
                "name": "psu",
                "available": "false",
                "instance_id": "psu0",
                "description": "the psu",
                "driver": "test",
            }
        ]

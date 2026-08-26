"""The capability registry: what it grants a run, and what it refuses."""

from __future__ import annotations

import pytest

from gauntlet.capabilities import CapabilityError, CapabilityRegistry, Grant
from gauntlet.instruments import MockPsu

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


class _Ownable(_Provider):
    """An ownable provider whose `own()` the test can make fail."""

    def __init__(self, name: str, *, opens: bool = True) -> None:
        super().__init__(name)
        self._opens = opens
        self._owned = False

    def owned(self) -> bool:
        return self._owned

    def own(self) -> bool:
        if self._opens:
            self._owned = True
        return self._owned

    def disown(self) -> None:
        self._owned = False


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

    def test_unregistering_returns_the_provider_and_forgets_it(self) -> None:
        registry = CapabilityRegistry()
        psu = _Provider("psu")
        registry.register(psu)
        assert registry.unregister("psu") is psu
        assert registry.names() == []

    def test_unregistering_a_name_that_was_never_there_is_none(self) -> None:
        assert CapabilityRegistry().unregister("psu") is None

    def test_an_unregistered_capability_is_missing(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("psu"))
        registry.unregister("psu")
        assert registry.missing(["psu"]) == ["psu"]

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


class TestClaimForRun:
    def test_owns_an_unowned_capability(self) -> None:
        registry = CapabilityRegistry()
        camera = _Ownable("camera")
        registry.register(camera)
        registry.claim_for_run(["camera"])
        assert camera.owned() is True

    def test_releasing_disowns_only_what_it_claimed(self) -> None:
        registry = CapabilityRegistry()
        camera = _Ownable("camera")
        registry.register(camera)
        release = registry.claim_for_run(["camera"])
        release()
        assert camera.owned() is False

    def test_a_capability_already_owned_is_left_owned_after_release(self) -> None:
        registry = CapabilityRegistry()
        camera = _Ownable("camera")
        camera.own()
        registry.register(camera)
        release = registry.claim_for_run(["camera"])
        release()
        assert camera.owned() is True

    def test_a_plain_capability_is_left_alone(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("psu"))
        release = registry.claim_for_run(["psu"])
        release()  # does not raise: nothing to own or disown

    def test_a_capability_that_will_not_open_fails_the_claim(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Ownable("camera", opens=False))
        with pytest.raises(CapabilityError, match="camera could not be opened"):
            registry.claim_for_run(["camera"])

    def test_a_failed_claim_releases_what_it_already_owned(self) -> None:
        registry = CapabilityRegistry()
        first = _Ownable("chamber")
        second = _Ownable("camera", opens=False)
        registry.register(first)
        registry.register(second)
        with pytest.raises(CapabilityError):
            registry.claim_for_run(["chamber", "camera"])
        assert first.owned() is False

    def test_release_is_idempotent(self) -> None:
        registry = CapabilityRegistry()
        camera = _Ownable("camera")
        registry.register(camera)
        release = registry.claim_for_run(["camera"])
        release()
        release()  # does not raise, and does not re-disown anything meaningful
        assert camera.owned() is False

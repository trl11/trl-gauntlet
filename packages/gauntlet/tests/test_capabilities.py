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

    def __init__(self, name: str, *, opens: bool = True, reason: str = "") -> None:
        super().__init__(name)
        self._opens = opens
        self._owned = False
        self._reason = reason

    def describe(self) -> dict[str, str]:
        return {**super().describe(), "unavailable_reason": self._reason}

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


class TestTwoOfOneInstrument:
    """A bench holding two bridges tells them apart by role."""

    def _bench(self) -> CapabilityRegistry:
        registry = CapabilityRegistry(api_base=_BASE)
        registry.register(_Provider("i2c"), role="dut")
        registry.register(_Provider("i2c"), role="ref")
        registry.register(_Provider("psu"))
        return registry

    def test_a_role_is_registered_under_its_own_key(self) -> None:
        assert self._bench().instance_keys() == ["i2c.dut", "i2c.ref", "psu"]

    def test_a_role_is_granted_its_own_url(self) -> None:
        grants = self._bench().grants(["i2c.dut", "i2c.ref"])
        assert [grant.url for grant in grants] == [
            f"{_BASE}/capabilities/i2c.dut",
            f"{_BASE}/capabilities/i2c.ref",
        ]

    def test_a_role_reaches_the_suite_as_a_doubled_underscore(self) -> None:
        assert self._bench().environment(["i2c.dut"]) == {
            "GAUNTLET_CAP_I2C__DUT_URL": f"{_BASE}/capabilities/i2c.dut",
            "GAUNTLET_CAP_I2C__DUT_ID": "i2c0",
        }

    def test_one_instrument_answers_a_bare_name_exactly_as_before(self) -> None:
        """A bench with one bridge is untouched by any of this."""
        registry = CapabilityRegistry(api_base=_BASE)
        registry.register(_Provider("i2c"))
        assert registry.resolve("i2c") == ["i2c"]
        assert registry.missing(["i2c"]) == []
        assert registry.grants(["i2c"])[0].url == f"{_BASE}/capabilities/i2c"

    def test_a_default_is_not_needed_where_only_one_is_plugged_in(self) -> None:
        """A role the bench named but whose device is absent settles nothing."""
        registry = CapabilityRegistry(api_base=_BASE)
        registry.register(_Provider("i2c"), role="ref")
        registry.set_defaults({"i2c": "dut"})
        assert registry.resolve("i2c") == ["i2c.ref"]
        assert registry.missing(["i2c"]) == []

    def test_a_bare_name_takes_the_default_the_bench_named(self) -> None:
        registry = self._bench()
        registry.set_defaults({"i2c": "ref"})
        assert registry.resolve("i2c") == ["i2c.ref"]
        assert registry.missing(["i2c"]) == []
        assert registry.grants(["i2c"])[0].url == f"{_BASE}/capabilities/i2c.ref"

    def test_a_default_never_overrides_a_role_that_was_asked_for(self) -> None:
        registry = self._bench()
        registry.set_defaults({"i2c": "ref"})
        assert registry.resolve("i2c.dut") == ["i2c.dut"]

    def test_a_default_reaches_the_suite_under_the_name_it_asked_for(self) -> None:
        """The suite said `i2c`, so that is the variable, whichever bridge it got."""
        registry = self._bench()
        registry.set_defaults({"i2c": "ref"})
        assert registry.environment(["i2c"]) == {
            "GAUNTLET_CAP_I2C_URL": f"{_BASE}/capabilities/i2c.ref",
            "GAUNTLET_CAP_I2C_ID": "i2c0",
        }

    def test_a_bare_name_is_refused_where_two_answer_to_it(self) -> None:
        with pytest.raises(CapabilityError) as caught:
            self._bench().grants(["i2c"])
        assert "i2c.dut, i2c.ref" in str(caught.value)

    def test_a_bare_name_is_unmet_where_two_answer_to_it(self) -> None:
        assert self._bench().missing(["i2c"]) == ["i2c"]

    def test_a_bare_name_still_finds_the_only_one_of_its_kind(self) -> None:
        """A suite needing one bus does not have to know its bench binds roles."""
        registry = CapabilityRegistry(api_base=_BASE)
        registry.register(_Provider("i2c"), role="dut")
        assert registry.missing(["i2c"]) == []
        assert registry.grants(["i2c"])[0].url == f"{_BASE}/capabilities/i2c.dut"

    def test_one_role_going_leaves_the_other_registered(self) -> None:
        registry = self._bench()
        registry.unregister("i2c.ref")
        assert registry.instance_keys() == ["i2c.dut", "psu"]
        assert registry.missing(["i2c.dut"]) == []


class TestRegistry:
    def test_names_are_sorted(self) -> None:
        registry = CapabilityRegistry()
        for name in ("psu", "chamber", "daq"):
            registry.register(_Provider(name))
        assert registry.instance_keys() == ["chamber", "daq", "psu"]

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
        assert registry.instance_keys() == []

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

    def test_a_failed_claim_carries_the_driver_reason(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Ownable("camera", opens=False, reason="no frame arrived"))
        with pytest.raises(CapabilityError, match="could not be opened: no frame arrived"):
            registry.claim_for_run(["camera"])

    def test_a_driver_with_no_reason_is_reported_without_one(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Ownable("camera", opens=False))
        with pytest.raises(CapabilityError) as caught:
            registry.claim_for_run(["camera"])
        assert str(caught.value).endswith("could not be opened")

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


class _Closable(_Provider):
    """A provider that records being closed."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class TestClosing:
    def test_every_provider_that_holds_a_device_is_released(self) -> None:
        registry = CapabilityRegistry()
        camera = _Closable("camera")
        psu = _Closable("psu")
        registry.register(camera)
        registry.register(psu)
        registry.close_all()
        assert (camera.closed, psu.closed) == (1, 1)

    def test_a_provider_with_nothing_to_release_is_left_alone(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_Provider("daq"))
        registry.close_all()

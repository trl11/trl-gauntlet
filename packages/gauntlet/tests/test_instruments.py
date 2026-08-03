"""The instrument API and the mock instruments behind it."""

from __future__ import annotations

import pytest

from gauntlet.capabilities import CommandRejected, MockChamber, MockDaq, MockPsu


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class TestInstrumentsApi:
    def test_lists_every_registered_instrument(self, client) -> None:
        instruments = client.get("/api/instruments").json()["instruments"]
        assert [i["name"] for i in instruments] == ["chamber", "daq", "psu"]
        for instrument in instruments:
            assert instrument["available"] is True
            assert instrument["instance_id"]
            assert instrument["description"]
            assert instrument["kind"] == instrument["name"]

    def test_commands_declare_their_fields(self, client) -> None:
        psu = client.get("/api/instruments/psu").json()
        set_voltage = next(c for c in psu["commands"] if c["name"] == "set_voltage")
        voltage = next(f for f in set_voltage["fields"] if f["name"] == "voltage")
        assert voltage == {
            "name": "voltage",
            "label": "Voltage",
            "type": "number",
            "unit": "V",
            "min": 0.0,
            "max": 30.0,
            "choices": [],
        }

    def test_scan_reports_the_same_instruments(self, client) -> None:
        # The readings move between the two calls; the instruments do not.
        scanned = client.post("/api/instruments/scan").json()["instruments"]
        listed = client.get("/api/instruments").json()["instruments"]
        assert [i["name"] for i in scanned] == [i["name"] for i in listed]
        assert [i["commands"] for i in scanned] == [i["commands"] for i in listed]

    def test_command_changes_the_state(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 7.5}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["result"] == {"channel": "1", "voltage_setpoint": 7.5}
        assert body["state"]["channels"]["1"]["voltage_setpoint"] == 7.5
        assert client.get("/api/instruments/psu").json()["state"]["channels"]["1"]["voltage_setpoint"] == 7.5

    def test_unknown_command_is_422(self, client) -> None:
        response = client.post("/api/instruments/psu/command", json={"command": "explode", "args": {}})
        assert response.status_code == 422
        assert "no command" in response.json()["detail"]

    def test_bad_argument_is_422(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 400}},
        )
        assert response.status_code == 422

    def test_unknown_instrument_is_404(self, client) -> None:
        assert client.get("/api/instruments/nope").status_code == 404
        assert client.post("/api/instruments/nope/command", json={"command": "x"}).status_code == 404

    def test_an_available_instrument_reports_no_reason(self, client) -> None:
        for instrument in client.get("/api/instruments").json()["instruments"]:
            assert instrument["unavailable_reason"] == ""

    def test_an_unavailable_instrument_reports_the_providers_reason(self, client) -> None:
        client.app.state.capabilities.register(_Bare())
        body = client.get("/api/instruments/bare").json()
        assert body["available"] is False
        assert body["unavailable_reason"] == "no device on /dev/ttyUSB9"

    def test_an_instrument_without_commands_is_405(self, client) -> None:
        client.app.state.capabilities.register(_Bare())
        response = client.post("/api/instruments/bare/command", json={"command": "x"})
        assert response.status_code == 405
        assert client.get("/api/instruments/bare").json()["commands"] == []
        assert client.get("/api/instruments/bare").json()["state"] == {}

    def test_a_provider_with_only_read_reports_that_as_its_state(self, client) -> None:
        client.app.state.capabilities.register(_ReadOnly())
        assert client.get("/api/instruments/readonly").json()["state"] == {"level": 3}

    def test_a_provider_that_names_no_kind_falls_back_to_its_name(self, client) -> None:
        client.app.state.capabilities.register(_Bare())
        assert client.get("/api/instruments/bare").json()["kind"] == "bare"

    def test_a_provider_with_a_model_but_no_description_uses_the_model(self, client) -> None:
        client.app.state.capabilities.register(_ReadOnly())
        assert client.get("/api/instruments/readonly").json()["description"] == "readonly-1"

    def test_a_body_without_a_command_is_422(self, client) -> None:
        assert client.post("/api/instruments/psu/command", json={"args": {}}).status_code == 422

    def test_an_unexpected_body_key_is_422(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_output", "args": {"channel": "1", "enabled": True}, "extra": 1},
        )
        assert response.status_code == 422

    def test_a_non_numeric_argument_is_422(self, client) -> None:
        response = client.post(
            "/api/instruments/psu/command",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": "twelve"}},
        )
        assert response.status_code == 422
        assert "must be a number" in response.json()["detail"]

    def test_scan_reprobes_availability(self, client) -> None:
        flaky = _Flaky()
        client.app.state.capabilities.register(flaky)
        assert client.get("/api/instruments/flaky").json()["available"] is False
        flaky.present = True
        scanned = client.post("/api/instruments/scan").json()["instruments"]
        assert next(i for i in scanned if i["name"] == "flaky")["available"] is True


class TestMockPsu:
    def test_output_off_reads_zero(self) -> None:
        channel = MockPsu(clock=_Clock()).state()["channels"]["1"]
        assert (channel["voltage"], channel["current"]) == (0.0, 0.0)
        assert channel["voltage_setpoint"] == 12.0

    def test_readback_tracks_the_setpoint_and_sags(self) -> None:
        psu = MockPsu(clock=_Clock())
        psu.command("set_output", {"channel": "1", "enabled": True})
        channel = psu.state()["channels"]["1"]
        assert 11.5 < channel["voltage"] < 12.0
        assert 1.2 < channel["current"] < 1.4

    def test_current_limit_caps_the_draw(self) -> None:
        psu = MockPsu(clock=_Clock())
        psu.command("set_current_limit", {"channel": "1", "current": 0.5})
        psu.command("set_output", {"channel": "1", "enabled": True})
        assert psu.state()["channels"]["1"]["current"] < 0.51

    def test_same_seed_and_clock_give_the_same_readings(self) -> None:
        readings = []
        for _ in range(2):
            psu = MockPsu(clock=_Clock(), seed=7)
            psu.command("set_output", {"channel": "1", "enabled": True})
            readings.append(psu.state())
        assert readings[0] == readings[1]

    def test_different_seeds_differ(self) -> None:
        states = []
        for seed in (1, 2):
            psu = MockPsu(clock=_Clock(), seed=seed)
            psu.command("set_output", {"channel": "1", "enabled": True})
            states.append(psu.state())
        assert states[0] != states[1]

    def test_readings_move_with_the_clock(self) -> None:
        clock = _Clock()
        psu = MockPsu(clock=clock)
        psu.command("set_output", {"channel": "1", "enabled": True})
        before = psu.state()
        clock.now += 5.0
        assert psu.state() != before

    def test_unknown_channel_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockPsu(clock=_Clock()).command("set_voltage", {"channel": "9", "voltage": 1.0})

    def test_non_boolean_output_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockPsu(clock=_Clock()).command("set_output", {"channel": "1", "enabled": "yes"})

    def test_unknown_command_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockPsu(clock=_Clock()).command("explode", {"channel": "1"})

    def test_a_missing_number_is_rejected(self) -> None:
        with pytest.raises(CommandRejected, match="must be a number"):
            MockPsu(clock=_Clock()).command("set_voltage", {"channel": "1"})

    def test_a_boolean_is_not_a_number(self) -> None:
        with pytest.raises(CommandRejected, match="must be a number"):
            MockPsu(clock=_Clock()).command("set_current_limit", {"channel": "1", "current": True})

    def test_read_is_the_state(self) -> None:
        psu = MockPsu(clock=_Clock())
        assert psu.read() == psu.state()

    def test_write_runs_a_command_and_returns_the_new_state(self) -> None:
        psu = MockPsu(clock=_Clock())
        state = psu.write({"command": "set_voltage", "args": {"channel": "2", "voltage": 3.3}})
        assert state["channels"]["2"]["voltage_setpoint"] == 3.3

    def test_write_without_a_command_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockPsu(clock=_Clock()).write({})


class TestMockDaq:
    def test_eight_analog_and_two_digital_channels(self) -> None:
        state = MockDaq(clock=_Clock()).state()
        assert len(state["channels"]) == 8
        assert len(state["digital"]) == 2
        assert state["channels"]["1"]["unit"] == "V"

    def test_set_range(self) -> None:
        daq = MockDaq(clock=_Clock())
        daq.command("set_range", {"channel": "3", "range_v": 1.0, "unit": "mV"})
        channel = daq.state()["channels"]["3"]
        assert (channel["range_v"], channel["unit"]) == (1.0, "mV")

    def test_range_clips_the_reading(self) -> None:
        daq = MockDaq(clock=_Clock())
        daq.command("set_range", {"channel": "8", "range_v": 0.1, "unit": "V"})
        assert daq.state()["channels"]["8"]["value"] == 0.1

    def test_tare_zeroes_the_channel(self) -> None:
        daq = MockDaq(clock=_Clock())
        daq.command("tare", {"channel": "4"})
        assert daq.state()["channels"]["4"]["value"] == 0.0

    def test_sample_scans_every_channel(self) -> None:
        sample = MockDaq(clock=_Clock()).command("sample", {})
        assert sorted(sample["analog"]) == [str(n) for n in range(1, 9)]
        assert sorted(sample["digital"]) == ["1", "2"]

    def test_readings_drift_with_the_clock(self) -> None:
        clock = _Clock()
        daq = MockDaq(clock=clock)
        before = daq.state()["channels"]["1"]["value"]
        clock.now += 7.0
        assert daq.state()["channels"]["1"]["value"] != before

    def test_same_seed_and_clock_give_the_same_readings(self) -> None:
        assert MockDaq(clock=_Clock(), seed=3).state() == MockDaq(clock=_Clock(), seed=3).state()

    def test_bad_unit_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockDaq(clock=_Clock()).command("set_range", {"channel": "1", "range_v": 1.0, "unit": "furlongs"})

    def test_a_rejected_set_range_changes_nothing(self) -> None:
        daq = MockDaq(clock=_Clock())
        before = daq.state()["channels"]["1"]
        with pytest.raises(CommandRejected):
            daq.command("set_range", {"channel": "1", "range_v": 1.0, "unit": "furlongs"})
        assert daq.state()["channels"]["1"] == before

    def test_unknown_command_is_rejected(self) -> None:
        with pytest.raises(CommandRejected, match="no command"):
            MockDaq(clock=_Clock()).command("calibrate", {})

    def test_unknown_channel_is_rejected(self) -> None:
        with pytest.raises(CommandRejected, match="no channel"):
            MockDaq(clock=_Clock()).command("tare", {"channel": "99"})

    def test_read_is_the_state(self) -> None:
        daq = MockDaq(clock=_Clock())
        assert daq.read() == daq.state()

    def test_write_runs_a_command_and_returns_the_new_state(self) -> None:
        daq = MockDaq(clock=_Clock())
        state = daq.write({"command": "set_range", "args": {"channel": "2", "range_v": 5.0, "unit": "A"}})
        assert state["channels"]["2"] == {"offset": 0.0, "range_v": 5.0, "unit": "A", "value": pytest.approx(1.0, 0.1)}


class TestMockChamber:
    def test_starts_at_ambient_and_idle(self) -> None:
        state = MockChamber(clock=_Clock()).state()
        assert state["running"] is False
        assert state["door_open"] is False
        assert state["actual_c"] == pytest.approx(22.0, abs=0.1)

    def test_ramps_toward_the_setpoint_while_running(self) -> None:
        clock = _Clock()
        chamber = MockChamber(clock=clock)
        chamber.command("set_setpoint", {"celsius": 60.0})
        chamber.command("start", {})
        clock.now += 10.0
        assert chamber.state()["actual_c"] == pytest.approx(26.0, abs=0.1)
        clock.now += 1_000.0
        assert chamber.state()["actual_c"] == pytest.approx(60.0, abs=0.1)

    def test_stopping_returns_it_to_ambient(self) -> None:
        clock = _Clock()
        chamber = MockChamber(clock=clock)
        chamber.command("set_setpoint", {"celsius": 60.0})
        chamber.command("start", {})
        clock.now += 1_000.0
        chamber.command("stop", {})
        clock.now += 1_000.0
        assert chamber.state()["actual_c"] == pytest.approx(22.0, abs=0.1)

    def test_same_seed_and_clock_give_the_same_readings(self) -> None:
        assert MockChamber(clock=_Clock(), seed=5).state() == MockChamber(clock=_Clock(), seed=5).state()

    def test_setpoint_out_of_range_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockChamber(clock=_Clock()).command("set_setpoint", {"celsius": 500.0})

    def test_unknown_command_is_rejected(self) -> None:
        with pytest.raises(CommandRejected):
            MockChamber(clock=_Clock()).command("defrost", {})

    def test_it_cools_when_the_setpoint_is_below_the_air(self) -> None:
        clock = _Clock()
        chamber = MockChamber(clock=clock)
        chamber.command("set_setpoint", {"celsius": -10.0})
        chamber.command("start", {})
        clock.now += 10.0
        assert chamber.state()["actual_c"] == pytest.approx(18.0, abs=0.1)
        clock.now += 1_000.0
        assert chamber.state()["actual_c"] == pytest.approx(-10.0, abs=0.1)

    def test_read_is_the_state(self) -> None:
        chamber = MockChamber(clock=_Clock())
        assert chamber.read() == chamber.state()

    def test_write_runs_a_command_and_returns_the_new_state(self) -> None:
        chamber = MockChamber(clock=_Clock())
        assert chamber.write({"command": "start"})["running"] is True


class _Bare:
    """A provider with nothing but the four required members."""

    name = "bare"

    def available(self) -> bool:
        return False

    def describe(self) -> dict[str, str]:
        return {"description": "nothing to see", "unavailable_reason": "no device on /dev/ttyUSB9"}

    def instance_id(self) -> str:
        return "bare0"


class _Flaky:
    """A provider whose availability changes between probes."""

    name = "flaky"

    def __init__(self) -> None:
        self.present = False

    def available(self) -> bool:
        return self.present

    def describe(self) -> dict[str, str]:
        return {"description": "comes and goes"}

    def instance_id(self) -> str:
        return "flaky0"


class _ReadOnly:
    """A provider that can be read but publishes no structured state."""

    name = "readonly"

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"model": "readonly-1"}

    def instance_id(self) -> str:
        return "readonly0"

    def read(self) -> dict[str, object]:
        return {"level": 3}

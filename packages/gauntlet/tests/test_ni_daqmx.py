"""The NI-DAQmx acquisition driver, and the choice between it and the DI-2008.

Every test here runs against a stand-in for the module, so none of them needs
hardware attached or the NI-DAQmx driver installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from gauntlet.capabilities import CapabilityRegistry, CommandRejected
from gauntlet.config import Settings
from gauntlet.instruments import detect_instruments
from gauntlet.instruments.ni_daqmx import (
    DaqmxTarget,
    NiDaqmxDaq,
    NiDaqmxError,
    as_channels,
    mode_name,
    open_daqmx,
    parse_target,
)


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeModule:
    """A stand-in NI-DAQmx module, answering with whatever the test sets."""

    def __init__(
        self,
        *,
        channels: tuple[str, ...] = ("ai0", "ai1"),
        ranges: tuple[float, ...] = (0.5,),
        rates: tuple[float, float] = (1613.0, 50000.0),
        samples: dict[str, list[float]] | None = None,
    ) -> None:
        self.closed = False
        self.reads: list[tuple[tuple[tuple[str, float], ...], float, int]] = []
        self.timeouts: list[float] = []
        self.fail_with: Exception | None = None
        self._channels = channels
        self._ranges = ranges
        self._rates = rates
        self._samples = samples or {name: [0.1, 0.3] for name in channels}

    def channels(self) -> tuple[str, ...]:
        return self._channels

    def close(self) -> None:
        self.closed = True

    def identity(self) -> dict[str, str]:
        return {"chassis": "cDAQ1", "model": "NI 9238", "serial": "01AB23CD"}

    def name(self) -> str:
        return "cDAQ1Mod1"

    def rate_limits(self) -> tuple[float, float]:
        return self._rates

    def read(
        self,
        channels: tuple[tuple[str, float], ...],
        rate_hz: float,
        samples: int,
        timeout_s: float = 2.0,
    ) -> list[list[float]]:
        self.reads.append((channels, rate_hz, samples))
        self.timeouts.append(timeout_s)
        if self.fail_with is not None:
            raise self.fail_with
        return [list(self._samples[name]) for name, _ in channels]

    def voltage_ranges(self) -> tuple[float, ...]:
        return self._ranges


def _daq(module: _FakeModule, clock: _Clock, **kwargs: Any) -> NiDaqmxDaq:
    return NiDaqmxDaq(clock=clock, open_module=lambda _: module, **kwargs)


class _Options:
    """A gRPC session, reduced to the channel the driver closes."""

    def __init__(self, channel: Any) -> None:
        self.grpc_channel = channel


def _refuse_local() -> Any:
    """The local driver, on a machine that has none."""
    raise AssertionError("a server target must not ask the local driver")


class _FakeChannel:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeDevice:
    """A device NI-DAQmx lists, with only what the driver looks at.

    ``chassis`` empty stands for a device that is not in one, which answers the
    question with an error rather than with nothing.
    """

    def __init__(
        self,
        name: str,
        channels: tuple[str, ...],
        *,
        chassis: str = "",
        product_type: str = "NI 9238",
        serial_num: int = 0x01AB23CD,
    ) -> None:
        self.name = name
        self.ai_physical_chans = [_FakeChannel(f"{name}/{channel}") for channel in channels]
        self.product_type = product_type
        self.serial_num = serial_num
        self._chassis = chassis

    @property
    def compact_daq_chassis_device(self) -> Any:
        from gauntlet.instruments import ni_daqmx

        if not self._chassis:
            raise ni_daqmx.nidaqmx.errors.DaqError("Requested property is not supported", -200452)
        return _FakeDevice(self._chassis, ())


class TestModeNames:
    """How a module's ranges are named, so a suite can ask for one."""

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [(0.5, "500mv"), (0.025, "25mv"), (0.1, "100mv"), (1.0, "1v"), (2.5, "2.5v"), (10.0, "10v")],
    )
    def test_a_range_is_named_the_way_the_di2008_names_its_own(self, limit: float, expected: str) -> None:
        assert mode_name(limit) == expected


class TestAcquisitionShape:
    """One acquisition, whatever shape NI-DAQmx answered in."""

    def test_several_channels_come_back_a_list_each(self) -> None:
        assert as_channels([[1.0, 2.0], [3.0, 4.0]]) == [[1.0, 2.0], [3.0, 4.0]]

    def test_one_channel_answers_flat_and_is_wrapped(self) -> None:
        assert as_channels([1.0, 2.0, 3.0]) == [[1.0, 2.0, 3.0]]

    def test_nothing_read_is_no_channels(self) -> None:
        assert as_channels([]) == []


class TestNiDaqmxDaq:
    """The provider, against a module that answers."""

    def test_it_takes_its_channels_from_the_module(self) -> None:
        module = _FakeModule(channels=("ai0", "ai1", "ai2", "ai3"))
        daq = _daq(module, _Clock())

        assert daq.available()
        assert set(daq.state()["channels"]) == {"ai0", "ai1", "ai2", "ai3"}

    def test_a_module_with_one_range_offers_exactly_that_one(self) -> None:
        daq = _daq(_FakeModule(ranges=(0.5,)), _Clock())
        daq.available()

        configure = next(command for command in daq.commands() if command["name"] == "configure")
        mode = next(field for field in configure["fields"] if field["name"] == "mode")
        assert mode["choices"] == ["500mv"]

    def test_channels_start_on_the_widest_range_the_module_offers(self) -> None:
        daq = _daq(_FakeModule(ranges=(0.2, 1.0, 10.0)), _Clock())
        daq.available()

        assert daq.state()["channels"]["ai0"]["mode"] == "10v"

    def test_it_runs_at_the_modules_slowest_rate(self) -> None:
        """A delta-sigma module will not run below its minimum at all."""
        module = _FakeModule(rates=(1613.0, 50000.0))
        daq = _daq(module, _Clock())
        daq.available()
        daq.command("sample", {})

        assert daq.sample_rate_hz() == 1613.0
        assert module.reads[-1][1] == 1613.0

    def test_a_reading_is_the_mean_of_the_acquisition(self) -> None:
        """There is no single-point read to make, so a reading is an average."""
        module = _FakeModule(channels=("ai0",), samples={"ai0": [0.1, 0.2, 0.3]})
        daq = _daq(module, _Clock())

        assert daq.command("sample", {})["channels"] == {"ai0": pytest.approx(0.2)}

    def test_the_acquisition_carries_each_channels_own_range(self) -> None:
        module = _FakeModule(channels=("ai0", "ai1"), ranges=(0.5, 10.0))
        daq = _daq(module, _Clock())
        daq.available()
        daq.command("configure", {"rows": {"ai0": {"mode": "500mv"}}})
        daq.command("sample", {})

        assert module.reads[-1][0] == (("ai0", 0.5), ("ai1", 10.0))

    def test_state_refreshes_a_stale_reading_and_reuses_a_fresh_one(self) -> None:
        clock = _Clock()
        module = _FakeModule()
        daq = _daq(module, clock, sample_interval_s=1.0)

        daq.state()
        taken = len(module.reads)
        daq.state()
        assert len(module.reads) == taken

        clock.advance(1.0)
        daq.state()
        assert len(module.reads) == taken + 1

    def test_it_describes_the_module_rather_than_the_chassis(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()

        assert daq.describe()["model"] == "NI 9238"
        assert daq.describe()["driver"] == "ni-daqmx"
        assert "cDAQ1Mod1" in daq.connection()
        assert "cDAQ1" in daq.connection()


class TestConfigure:
    """Settling channels, one row each."""

    def test_a_label_names_the_readings_of_its_channel(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()
        daq.command("configure", {"rows": {"ai0": {"label": "Shunt A"}}})

        labels = [entry["label"] for entry in daq.readouts()]
        assert "Shunt A" in labels

    def test_an_unnamed_channel_falls_back_to_its_number(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()

        assert [entry["label"] for entry in daq.readouts()] == ["AI 0", "AI 1"]

    def test_an_emptied_label_puts_the_channel_back_to_its_number(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()
        daq.command("configure", {"rows": {"ai0": {"label": "Shunt A"}}})
        daq.command("configure", {"rows": {"ai0": {"label": "  "}}})

        assert daq.state()["channels"]["ai0"]["label"] == "AI 0"

    def test_a_channel_no_row_names_is_left_alone(self) -> None:
        daq = _daq(_FakeModule(ranges=(0.5, 10.0)), _Clock())
        daq.available()
        daq.command("configure", {"rows": {"ai0": {"mode": "500mv"}}})

        channels = daq.state()["channels"]
        assert channels["ai0"]["mode"] == "500mv"
        assert channels["ai1"]["mode"] == "10v"

    def test_a_bad_range_leaves_every_row_as_it_was(self) -> None:
        daq = _daq(_FakeModule(ranges=(0.5, 10.0)), _Clock())
        daq.available()

        with pytest.raises(CommandRejected, match="'mode' must be one of"):
            daq.command("configure", {"rows": {"ai0": {"mode": "500mv"}, "ai1": {"mode": "1000v"}}})
        assert daq.state()["channels"]["ai0"]["mode"] == "10v"

    def test_an_unknown_channel_is_rejected(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()

        with pytest.raises(CommandRejected, match="no channel 'ai9'"):
            daq.command("configure", {"rows": {"ai9": {"label": "x"}}})

    def test_rows_are_required(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()

        with pytest.raises(CommandRejected, match="must name at least one channel"):
            daq.command("configure", {})

    def test_an_unknown_command_is_rejected(self) -> None:
        daq = _daq(_FakeModule(), _Clock())
        daq.available()

        with pytest.raises(CommandRejected, match="no command 'tare'"):
            daq.command("tare", {})


class TestCapture:
    """Keeping the samples rather than the mean of them."""

    def _daq_with(self, samples: dict[str, list[float]]) -> Any:
        daq = _daq(_FakeModule(samples=samples), _Clock())
        daq.available()
        return daq

    def test_a_capture_answers_with_every_sample(self) -> None:
        daq = self._daq_with({"ai0": [0.1, 0.2, 0.3], "ai1": [-0.1, 0.0, 0.1]})

        captured = daq.command("capture", {"rate_hz": 25000.0, "samples": 3})

        assert captured["rate_hz"] == 25000.0
        assert captured["samples"] == 3
        assert captured["channels"]["ai0"]["values"] == [0.1, 0.2, 0.3]
        assert captured["channels"]["ai1"]["values"] == [-0.1, 0.0, 0.1]

    def test_a_capture_measures_the_window_it_took(self) -> None:
        daq = self._daq_with({"ai0": [0.1, 0.3], "ai1": [0.0, 0.0]})

        measured = daq.command("capture", {"rate_hz": 25000.0, "samples": 2})["channels"]["ai0"]

        assert measured["min"] == 0.1
        assert measured["max"] == 0.3
        assert measured["mean"] == 0.2
        assert measured["peak_to_peak"] == pytest.approx(0.2)

    def test_the_window_is_what_the_read_is_given_to_finish_in(self) -> None:
        module = _FakeModule(samples={"ai0": [0.1], "ai1": [0.1]})
        daq = _daq(module, _Clock())
        daq.available()

        daq.command("capture", {"rate_hz": 1613.0, "samples": 16130})

        # Ten seconds of signal cannot arrive inside the limit a panel reading
        # is held to, so a capture is given its own window and then some.
        assert module.timeouts[-1] > 10.0

    def test_the_state_carries_what_the_last_capture_came_to(self) -> None:
        daq = self._daq_with({"ai0": [0.1, 0.3], "ai1": [0.0, 0.0]})
        daq.command("capture", {"rate_hz": 25000.0, "samples": 2})

        last = daq.state()["last_capture"]
        assert last["rate_hz"] == 25000.0
        assert last["samples"] == 2
        assert last["channels"]["ai0"]["peak_to_peak"] == pytest.approx(0.2)
        # The samples are the caller's to keep; a second copy here would serve
        # nobody and every capture would grow the panel's reply.
        assert "values" not in last["channels"]["ai0"]

    def test_the_last_sample_stands_as_the_reading(self) -> None:
        daq = self._daq_with({"ai0": [0.1, 0.35], "ai1": [0.0, 0.0]})
        daq.command("capture", {"rate_hz": 25000.0, "samples": 2})

        assert daq.state()["channels"]["ai0"]["value"] == 0.35

    def test_a_rate_the_module_does_not_run_at_is_rejected(self) -> None:
        daq = self._daq_with({"ai0": [0.1], "ai1": [0.1]})

        with pytest.raises(CommandRejected, match="'rate_hz' must be between"):
            daq.command("capture", {"rate_hz": 200000.0, "samples": 10})

    def test_more_samples_than_one_reply_can_carry_are_rejected(self) -> None:
        daq = self._daq_with({"ai0": [0.1], "ai1": [0.1]})

        with pytest.raises(CommandRejected, match="'samples' must be between"):
            daq.command("capture", {"rate_hz": 25000.0, "samples": 1_000_000})

    def test_a_capture_answers_with_itself_through_the_capability(self) -> None:
        daq = self._daq_with({"ai0": [0.1, 0.3], "ai1": [0.0, 0.0]})

        written = daq.write({"command": "capture", "args": {"rate_hz": 25000.0, "samples": 2}})

        assert written["channels"]["ai0"]["values"] == [0.1, 0.3]

    def test_the_capture_command_offers_the_module_s_own_rates(self) -> None:
        daq = self._daq_with({"ai0": [0.1], "ai1": [0.1]})

        capture = next(entry for entry in daq.commands() if entry["name"] == "capture")
        rate = next(field for field in capture["fields"] if field["name"] == "rate_hz")
        assert (rate["min"], rate["max"]) == (1613.0, 50000.0)


class TestUnavailable:
    """A module that is not there, or that stopped answering."""

    def test_a_module_that_never_answers_says_why(self) -> None:
        def refuse(_: str) -> Any:
            raise NiDaqmxError("NI-DAQmx lists no analog input device")

        daq = NiDaqmxDaq(clock=_Clock(), open_module=refuse)

        assert not daq.available()
        assert daq.describe()["unavailable_reason"] == "NI-DAQmx lists no analog input device"
        assert daq.state()["channels"] == {}

    def test_a_command_on_an_absent_module_is_rejected(self) -> None:
        def refuse(_: str) -> Any:
            raise NiDaqmxError("NI-DAQmx did not answer")

        daq = NiDaqmxDaq(clock=_Clock(), open_module=refuse)

        with pytest.raises(CommandRejected, match="daq is unavailable"):
            daq.command("sample", {})

    def test_a_module_is_reprobed_at_most_once_an_interval(self) -> None:
        clock = _Clock()
        attempts = []

        def refuse(_: str) -> Any:
            attempts.append(clock.now)
            raise NiDaqmxError("nothing there")

        daq = NiDaqmxDaq(clock=clock, open_module=refuse, probe_interval_s=3.0)
        daq.available()
        daq.available()
        assert len(attempts) == 1

        clock.advance(3.0)
        daq.available()
        assert len(attempts) == 2

    def test_a_module_reporting_no_range_is_refused_rather_than_registered(self) -> None:
        daq = _daq(_FakeModule(ranges=()), _Clock())

        assert not daq.available()
        assert "no input range" in daq.describe()["unavailable_reason"]

    def test_a_module_reporting_no_channel_is_refused(self) -> None:
        daq = _daq(_FakeModule(channels=()), _Clock())

        assert not daq.available()
        assert "no analog input" in daq.describe()["unavailable_reason"]

    def test_a_failed_acquisition_drops_the_module_and_says_why(self) -> None:
        clock = _Clock()
        module = _FakeModule()
        daq = _daq(module, clock, probe_interval_s=3.0)
        daq.command("sample", {})

        module.fail_with = OSError("the module went away")
        clock.advance(1.0)
        daq.state()

        assert module.closed
        assert "acquisition failed" in daq.describe()["unavailable_reason"]

    def test_a_failed_acquisition_keeps_the_last_reading(self) -> None:
        clock = _Clock()
        module = _FakeModule(channels=("ai0",), samples={"ai0": [0.4, 0.4]})
        daq = _daq(module, clock)
        daq.command("sample", {})

        module.fail_with = OSError("gone")
        clock.advance(1.0)
        assert daq.state()["channels"]["ai0"]["value"] == pytest.approx(0.4)

    def test_closing_releases_the_module(self) -> None:
        module = _FakeModule()
        daq = _daq(module, _Clock())
        daq.available()
        daq.close()

        assert module.closed


class TestOpenDaqmx:
    """Which of the devices NI-DAQmx lists is the one to drive."""

    def _system(self, monkeypatch: Any, devices: list[Any]) -> None:
        from gauntlet.instruments import ni_daqmx

        class _System:
            def __init__(self) -> None:
                self.devices = devices

        monkeypatch.setattr(ni_daqmx.nidaqmx.system.System, "local", staticmethod(_System))

    def test_a_chassis_without_channels_is_passed_over(self, monkeypatch: Any) -> None:
        """NI-DAQmx lists the chassis beside the module, and only one is readable."""
        self._system(monkeypatch, [_FakeDevice("cDAQ1", ()), _FakeDevice("cDAQ1Mod1", ("ai0",))])

        assert open_daqmx(DaqmxTarget()).name() == "cDAQ1Mod1"

    def test_a_named_device_is_the_one_taken(self, monkeypatch: Any) -> None:
        self._system(monkeypatch, [_FakeDevice("cDAQ1Mod1", ("ai0",)), _FakeDevice("Dev2", ("ai0",))])

        assert open_daqmx(DaqmxTarget(device="Dev2")).name() == "Dev2"

    def test_a_named_device_that_is_not_listed_is_an_error(self, monkeypatch: Any) -> None:
        self._system(monkeypatch, [_FakeDevice("cDAQ1Mod1", ("ai0",))])

        with pytest.raises(NiDaqmxError, match="no analog input device named 'Dev2'"):
            open_daqmx(DaqmxTarget(device="Dev2"))

    def test_an_empty_system_is_an_error(self, monkeypatch: Any) -> None:
        self._system(monkeypatch, [])

        with pytest.raises(NiDaqmxError, match="lists no analog input device"):
            open_daqmx(DaqmxTarget())


class TestTargets:
    """What a ``daqmx:`` setting names."""

    def test_a_bare_name_is_a_local_device(self) -> None:
        assert parse_target("cDAQ1Mod1") == DaqmxTarget(device="cDAQ1Mod1")

    def test_nothing_at_all_is_the_local_driver_and_any_device(self) -> None:
        assert parse_target("") == DaqmxTarget()

    def test_an_address_alone_is_a_server_and_any_device_on_it(self) -> None:
        assert parse_target("//bench:31763") == DaqmxTarget(server="bench:31763")

    def test_an_address_and_a_name_is_that_device_on_that_server(self) -> None:
        assert parse_target("//bench:31763/cDAQ1Mod1") == DaqmxTarget(device="cDAQ1Mod1", server="bench:31763")


class TestIdentity:
    """What the driver reports about the device NI-DAQmx handed it."""

    def _module(self, device: _FakeDevice) -> Any:
        from gauntlet.instruments.ni_daqmx import _DaqmxModule

        return _DaqmxModule(device)

    def test_a_module_names_the_chassis_it_sits_in(self) -> None:
        identity = self._module(_FakeDevice("cDAQ1Mod1", ("ai0",), chassis="cDAQ1")).identity()

        assert identity == {"chassis": "cDAQ1", "model": "NI 9238", "serial": "01AB23CD"}

    def test_a_device_that_is_its_own_chassis_is_still_readable(self) -> None:
        """Asking must not be what decides whether the module can be driven."""
        identity = self._module(_FakeDevice("Dev1", ("ai0",), product_type="USB-6000")).identity()

        assert identity["chassis"] == ""
        assert identity["model"] == "USB-6000"

    def test_a_device_reporting_no_serial_reports_none(self) -> None:
        identity = self._module(_FakeDevice("Dev1", ("ai0",), serial_num=0)).identity()

        assert identity["serial"] == ""

    def test_channels_are_named_without_their_device(self) -> None:
        assert self._module(_FakeDevice("cDAQ1Mod1", ("ai0", "ai1"))).channels() == ("ai0", "ai1")


class TestGrpcServer:
    """Reaching a driver on another machine, which is what a container does."""

    def _grpc(self, monkeypatch: Any, devices: list[Any] | None = None) -> list[Any]:
        """Stand in for grpc and for the remote system, returning the channels made."""
        import grpc

        from gauntlet.instruments import ni_daqmx

        channels: list[Any] = []

        class _Channel:
            def __init__(self, address: str) -> None:
                self.address = address
                self.closed = False
                channels.append(self)

            def close(self) -> None:
                self.closed = True

        class _System:
            def __init__(self) -> None:
                self.devices = devices if devices is not None else []

        monkeypatch.setattr(grpc, "insecure_channel", _Channel)
        monkeypatch.setattr(ni_daqmx.nidaqmx, "GrpcSessionOptions", lambda channel, name: _Options(channel))
        monkeypatch.setattr(ni_daqmx.nidaqmx.system.System, "remote", staticmethod(lambda options: _System()))
        monkeypatch.setattr(ni_daqmx.nidaqmx.system.System, "local", staticmethod(_refuse_local))
        return channels

    def test_a_server_target_asks_that_server_rather_than_this_machine(self, monkeypatch: Any) -> None:
        channels = self._grpc(monkeypatch, [_FakeDevice("cDAQ1Mod1", ("ai0",), chassis="cDAQ1")])

        module = open_daqmx(DaqmxTarget(server="bench:31763"))

        assert module.name() == "cDAQ1Mod1"
        assert [channel.address for channel in channels] == ["bench:31763"]

    def test_the_channel_is_dropped_when_no_device_answers(self, monkeypatch: Any) -> None:
        """A probe every few seconds would otherwise leave a channel behind each time."""
        channels = self._grpc(monkeypatch, [])

        with pytest.raises(NiDaqmxError, match="lists no analog input device"):
            open_daqmx(DaqmxTarget(server="bench:31763"))
        assert [channel.closed for channel in channels] == [True]

    def test_closing_the_module_drops_the_channel(self, monkeypatch: Any) -> None:
        channels = self._grpc(monkeypatch, [_FakeDevice("cDAQ1Mod1", ("ai0",))])

        open_daqmx(DaqmxTarget(server="bench:31763")).close()

        assert [channel.closed for channel in channels] == [True]

    def test_a_server_that_is_not_listening_says_so(self, monkeypatch: Any) -> None:
        from gauntlet.instruments import ni_daqmx

        self._grpc(monkeypatch)

        def refuse(_: Any) -> Any:
            raise ni_daqmx.nidaqmx.errors.RpcError(-1, "failed to connect")

        monkeypatch.setattr(ni_daqmx.nidaqmx.system.System, "remote", staticmethod(refuse))
        with pytest.raises(NiDaqmxError, match="gRPC device server at bench:31763 did not answer"):
            open_daqmx(DaqmxTarget(server="bench:31763"))


class TestDetection:
    """Which acquisition driver detection builds."""

    def _settings(self, tmp_path: Any, **overrides: Any) -> Settings:
        return Settings(
            data_dir=tmp_path / "data",
            **{
                "camera_device": "",
                "daq_serial": "",
                "i2c_serial": "",
                "logic_serial": "",
                "psu_port": "",
                **overrides,
            },
        )

    def test_a_daqmx_prefix_names_an_ni_device(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        seen: list[str] = []

        def build(**kwargs: Any) -> Any:
            seen.append(kwargs["target"])
            return NiDaqmxDaq(clock=_Clock(), open_module=lambda _: _FakeModule(), **kwargs)

        monkeypatch.setattr(detect, "NiDaqmxDaq", build)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="daqmx:cDAQ1Mod1"))

        daq = registry.provider("daq")
        assert daq is not None
        assert daq.describe()["driver"] == "ni-daqmx"
        assert seen == [DaqmxTarget(device="cDAQ1Mod1")]

    def test_auto_falls_through_to_ni_when_no_di2008_answers(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect
        from gauntlet.instruments.di2008_daq import Di2008Daq, Di2008Error

        def refuse(_: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        monkeypatch.setattr(detect, "Di2008Daq", lambda **kwargs: Di2008Daq(open_transport=refuse, **kwargs))
        monkeypatch.setattr(
            detect,
            "NiDaqmxDaq",
            lambda **kwargs: NiDaqmxDaq(clock=_Clock(), open_module=lambda _: _FakeModule(), **kwargs),
        )
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))

        daq = registry.provider("daq")
        assert daq is not None
        assert daq.describe()["driver"] == "ni-daqmx"

    def test_a_daqmx_url_names_a_server(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        seen: list[DaqmxTarget] = []

        def build(**kwargs: Any) -> Any:
            seen.append(kwargs["target"])
            return NiDaqmxDaq(clock=_Clock(), open_module=lambda _: _FakeModule(), **kwargs)

        monkeypatch.setattr(detect, "NiDaqmxDaq", build)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="daqmx://bench:31763/cDAQ1Mod1"))

        assert seen == [DaqmxTarget(device="cDAQ1Mod1", server="bench:31763")]

    def test_a_bare_serial_is_still_a_di2008(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="DAQ-42"))

        daq = registry.provider("daq")
        assert daq is not None
        assert daq.describe()["driver"] == "di2008"

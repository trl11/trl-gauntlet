"""The endpoints a suite drives in place of opening the device."""

from __future__ import annotations


class TestCapabilities:
    def test_reads_a_mock(self, client) -> None:
        assert "channels" in client.get("/api/capabilities/psu").json()

    def test_writing_drives_a_command(self, client) -> None:
        body = client.post(
            "/api/capabilities/psu",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 9.0}},
        )
        assert body.status_code == 200
        assert body.json()["channels"]["1"]["voltage_setpoint"] == 9.0

    def test_a_refused_command_is_422_carrying_the_provider_s_words(self, client) -> None:
        """The same answer /api/instruments gives, since a suite reads this one.

        A 500 would have a run report a server fault where it in fact asked
        for something the instrument does not offer.
        """
        body = client.post(
            "/api/capabilities/psu",
            json={"command": "set_voltage", "args": {"channel": "1", "voltage": 9000.0}},
        )
        assert body.status_code == 422
        assert "voltage" in body.json()["detail"]

    def test_an_unknown_command_is_refused_rather_than_raised(self, client) -> None:
        body = client.post("/api/capabilities/psu", json={"command": "explode", "args": {}})
        assert body.status_code == 422

    def test_this_router_lists_nothing(self, client) -> None:
        """A suite is handed its grants; the bench is listed under /instruments."""
        assert client.get("/api/capabilities").status_code == 404

    def test_a_provider_that_cannot_be_read_is_405(self, client) -> None:
        client.app.state.capabilities.register(_Opaque())
        assert client.get("/api/capabilities/opaque").status_code == 405

    def test_a_provider_that_cannot_be_written_is_405(self, client) -> None:
        client.app.state.capabilities.register(_Opaque())
        assert client.post("/api/capabilities/opaque", json={}).status_code == 405

    def test_unknown_capability_is_404(self, client) -> None:
        assert client.get("/api/capabilities/nope").status_code == 404
        assert client.post("/api/capabilities/nope", json={}).status_code == 404


class _Opaque:
    """A provider with nothing but the four required members."""

    name = "opaque"

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"description": "write-only in the worst sense"}

    def instance_id(self) -> str:
        return "opaque0"

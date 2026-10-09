"""What the bench check reads from the unit, and what it refuses to start on."""

from __future__ import annotations

from suite.preflight import Interface, parse, problems

CONTROL = Interface(name="eth0", present=True, link=True, address="192.168.0.61")
PART = Interface(name="eth1", present=True, link=True, address="192.168.0.45")


def test_the_unit_s_report_reads_as_two_interfaces_and_the_address_reached() -> None:
    control, part, reached, via = parse("eth0 1 192.168.0.61\neth1 0 -\nreached 192.168.0.61 eth0\n", "eth0", "eth1")
    assert control == CONTROL
    assert part == Interface(name="eth1", present=True, link=False, address="")
    assert (reached, via) == ("192.168.0.61", "eth0")


def test_an_interface_the_unit_does_not_have_reads_as_missing() -> None:
    _, part, _, _ = parse("eth0 1 192.168.0.61\neth1 missing -\nreached 192.168.0.61 eth0\n", "eth0", "eth1")
    assert not part.present


def test_a_correct_bench_has_no_problem() -> None:
    assert problems(CONTROL, PART, "192.168.0.61", "eth0") == []


def test_a_link_local_target_on_eth0_is_accepted() -> None:
    assert problems(CONTROL, PART, "fe80::880b:889d:4240:a6d1%eth0", "eth0") == []


def test_targeting_the_lan7430_is_refused_and_names_eth0_s_address() -> None:
    (problem,) = problems(CONTROL, PART, "192.168.0.45", "eth1")
    assert "192.168.0.45 is eth1's address" in problem
    assert "Set the run target to eth0's address, 192.168.0.61" in problem


def test_targeting_some_other_address_is_refused() -> None:
    (problem,) = problems(CONTROL, PART, "192.168.10.136", "wlan0")
    assert problem.endswith("Set the run target to 192.168.0.61.")


def test_a_lan7430_with_no_link_is_named() -> None:
    part = Interface(name="eth1", present=True, link=False, address="")
    (problem,) = problems(CONTROL, part, "192.168.0.61", "eth0")
    assert problem.startswith("eth1 (the LAN7430) has no link.")


def test_a_missing_lan7430_is_named() -> None:
    part = Interface(name="eth1", present=False, link=False, address="")
    (problem,) = problems(CONTROL, part, "192.168.0.61", "eth0")
    assert "the LAN7430 is not detected" in problem


def test_a_control_port_with_no_link_and_a_target_elsewhere_are_both_named() -> None:
    control = Interface(name="eth0", present=True, link=False, address="")
    found = problems(control, PART, "192.168.0.45", "eth1")
    assert found[0].startswith("eth0 has no link.")
    assert "Set the run target to eth0's address," in found[1]


def test_describe_shows_link_and_address() -> None:
    assert CONTROL.describe() == "eth0: link up, 192.168.0.61"
    assert Interface(name="eth1", present=True, link=False, address="").describe() == "eth1: no link, no IPv4 address"
    assert Interface(name="eth1", present=False, link=False, address="").describe() == "eth1: not present"

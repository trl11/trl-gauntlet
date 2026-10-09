"""Checking the unit is cabled and addressed the way the measurement needs.

Two of the unit's interfaces matter. The control interface (eth0) is how
Gauntlet reaches the unit over SSH. The part's interface (eth1) is the LAN7430
carrying the traffic under test. Each needs a link and an IPv4 address, and the
run target has to be the control interface's address: control traffic through
the part dies with it, and takes the record of its failure along.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gauntlet_sdk.remote import RemoteError, run, shell_quote


@dataclass(frozen=True)
class Interface:
    """One of the unit's interfaces as the unit reports it."""

    name: str
    present: bool
    link: bool
    address: str

    def describe(self) -> str:
        """The interface as one line for the run log, e.g. `eth0: link up, 192.168.0.61`."""
        if not self.present:
            return f"{self.name}: not present"
        link = "link up" if self.link else "no link"
        return f"{self.name}: {link}, {self.address or 'no IPv4 address'}"


def read(client: Any, control: str, part: str, *, timeout: float) -> tuple[Interface, Interface, str, str]:
    """Both interfaces, and the address and interface the SSH session reached the unit on."""
    lines = []
    for name in (control, part):
        quoted = shell_quote(name)
        lines.append(
            f"if [ -d /sys/class/net/{quoted} ]; then c=$(cat /sys/class/net/{quoted}/carrier 2>/dev/null || echo 0); "
            f"else c=missing; fi; "
            f"a=$(ip -4 -oneline address show dev {quoted} 2>/dev/null | awk '{{print $4}}' | cut -d/ -f1 | head -n1); "
            f'echo "{name} $c ${{a:--}}"'
        )
    # SSH_CONNECTION is "client port server port": the third field is the
    # address of the unit this session came in on. The interface holding it is
    # what gets compared, because a link-local IPv6 target arrives as
    # `fe80::1%eth0` and matches no IPv4 address.
    lines.append(
        "set -- $SSH_CONNECTION; r=${3%%%*}; "
        'i=$(ip -oneline address | awk -v r="$r" \'{split($4, a, "/"); if (a[1] == r) print $2}\' | head -n1); '
        'echo "reached ${3:--} ${i:--}"'
    )
    result = run(client, "; ".join(lines), timeout=timeout)
    if not result.ok:
        raise RemoteError(f"reading the unit's interfaces: {result.output}")
    return parse(result.stdout, control, part)


def parse(text: str, control: str, part: str) -> tuple[Interface, Interface, str, str]:
    """What ``read``'s command printed: two interfaces, then the address and interface reached."""
    found: dict[str, Interface] = {}
    reached = via = ""
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "reached":
            reached, via = ("" if value == "-" else value for value in fields[1:])
        elif len(fields) == 3:
            name, carrier, address = fields
            found[name] = Interface(
                name=name,
                present=carrier != "missing",
                link=carrier == "1",
                address="" if address == "-" else address,
            )
    return (
        found.get(control) or Interface(name=control, present=False, link=False, address=""),
        found.get(part) or Interface(name=part, present=False, link=False, address=""),
        reached,
        via,
    )


def problems(control: Interface, part: Interface, reached: str, via: str) -> list[str]:
    """Everything wrong with the setup, each as a sentence a tester can act on.

    ``reached`` is the address the run target reached the unit on and ``via``
    the interface holding it.
    """
    found = []
    if not control.present:
        found.append(
            f"The unit has no {control.name}. It is the interface Gauntlet controls the unit through; "
            "check interface.control_name in the profile."
        )
    elif not control.link:
        found.append(
            f"{control.name} has no link. Plug the unit's built-in Ethernet port into the lab network: "
            "it is how Gauntlet controls the unit."
        )
    elif not control.address:
        found.append(f"{control.name} has a link but no IPv4 address. Check DHCP on the lab network.")

    if not part.present:
        found.append(
            f"The unit has no {part.name}, so the LAN7430 is not detected. "
            "Check the card is seated and shows in lspci, then power-cycle the unit."
        )
    elif not part.link:
        found.append(
            f"{part.name} (the LAN7430) has no link. Plug the LAN7430's port into the lab network "
            "and check the port's lights."
        )
    elif not part.address:
        found.append(f"{part.name} (the LAN7430) has a link but no IPv4 address. Check DHCP on the lab network.")

    if via == part.name:
        found.append(
            f"The run target {reached} is {part.name}'s address, the LAN7430 under test. "
            f"Set the run target to {control.name}'s address"
            + (f", {control.address}" if control.address else "")
            + ", so control traffic does not go through the part."
        )
    elif via != control.name:
        found.append(
            f"The run target reached the unit on {reached or 'an unknown address'}, "
            f"which is not {control.name}'s address. "
            + (f"Set the run target to {control.address}." if control.address else "")
        )
    return found

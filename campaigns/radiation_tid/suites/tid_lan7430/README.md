# tid_lan7430 — bench setup

What the suite measures and how it decides is in the module docstrings, chiefly
[`suite/runner.py`](suite/runner.py). This file is the part that lives on the
bench rather than in the code: what has to be true before a run will work.

## The measurement only means something if the traffic crosses the part

The controller sits in the host's M.2 slot and presents an interface — `eth1`
on the current bench. The iperf3 **server runs on the host, bound to that
interface's own address**, and the client runs lab-side. Binding the server
that way is what forces the host's replies out through the LAN7430 instead of
its built-in interface.

That matters here because the two subnets are routed together: the host is
reachable at more than one address, and a run pointed at the wrong one would
produce a healthy-looking gigabit number measured entirely on the wrong part.
So every tick also compares the interface's own byte counters against what
iperf3 reported moving, and records `topology/traffic_bypassed_interface` when
they disagree by more than half. **If you see that anomaly, the numbers in the
run are not about the LAN7430.**

Run `profiles/bench.yaml` once before a beam run. It is two minutes and exists
to catch exactly this, along with a link that came up at 100 Mbps and an OTP
that cannot be read.

## Naming both ends of the path

Both ends are configurable, and on a bench where the two subnets are routed
together both are worth pinning.

| Setting | End | Effect |
|---|---|---|
| `interface.address` | ingress, on the unit | the address the iperf3 server binds to. Empty reads it from `interface.name` on the unit |
| `iperf.lab_address` | egress, on the lab host | the source address the client sends from. Empty lets the kernel's routing table choose |

Leaving `lab_address` empty is fine when only one route exists. When more than
one does, the kernel picks, and which interface the measurement actually left
from is then not something the run recorded.

## Testing it without the part

`tools/mock-bench.sh` builds a containerised stand-in: a unit reachable on two
networks, one carrying SSH and one standing in for the controller, with the
data path shapeable. It exercises the real driver path — SSH, the collector,
address resolution, the server, the route cross-check — where `driver: mock`
only exercises the analysis.

```
./tools/mock-bench.sh up
./tools/mock-bench.sh shape 100mbit 2% 5ms
./tools/mock-bench.sh run profiles/bench.yaml
./tools/mock-bench.sh down
```

Shaping shows up where it should: unshaped the loopback-speed path measures
tens of gigabits, `100mbit 2% 5ms` takes transmit to about 30 Mbps, and
`50mbit 3% 10ms` to about 9 Mbps.

Two things it cannot stand in for. A veth has no OTP, no register dump and no
PCIe device, so those probes report unreadable rather than comparing — which
is worth seeing once, because it is exactly how the suite behaves on a unit
without `sudo`. And shaping applies to the unit's egress, so it moves transmit
far more than receive.

Traffic shaping needs `NET_ADMIN`, which this devcontainer does not hold, so
the unit runs as its own container through the host's docker daemon and the
devcontainer joins the data network to reach it. `down` detaches it again.

## What the host needs

| Requirement | Why | Check |
|---|---|---|
| SSH by **key**, not password | The SDK authenticates with a key and never sends a password | `ssh -i <key> trl@<host> true` |
| `iperf3` | the throughput measurement | `iperf3 --version` |
| `ethtool` | link state, driver counters, OTP and register dumps | `ssh <host> /usr/sbin/ethtool -i eth1` |
| passwordless `sudo` | OTP, register dump and `dmesg` need root | `sudo -n true` |
| the interface up with an IPv4 address | the server binds to it; the address is read from the host at setup | `ip -4 addr show eth1` |

Without sudo the run still works — the OTP and register checks report
`otp/unreadable` instead of comparing, and everything else is unaffected.

`ethtool` lives in `/usr/sbin`, which a non-interactive SSH login does not put
on `PATH`, so it is not found by name the way an interactive shell finds it.
The collector resolves it by absolute path; a check run by hand over SSH has to
spell the path out, which is why the command above does.

## What the lab host needs

`iperf3`, because the client runs there. The suite fails setup with a clear
message if it is missing rather than reporting zero throughput.

## The login

The profile carries it, so a run started from the app needs nothing exported.
A run inherits the environment of whatever shell launched the server, which
made an exported login the one thing nobody remembered to set — and the run
then failed against `root` rather than saying what was missing.

| Setting | Default | Effect |
|---|---|---|
| `ssh_user` | `trl` | login on the unit |
| `ssh_key_path` | empty | private key. Empty uses the engineering key below |

Both are `overrides:` as well, so the app's run form and `--ssh-user` /
`--ssh-key-path` set them per run without editing a profile.

The key is `saver/id_ed_saver_eng_key` from the `extras/trl-engineering-keys`
submodule, found by walking up from the suite to the repository root. Nothing
is copied into `~/.ssh` and nothing is exported. A checkout without the
submodule initialised falls back to `GAUNTLET_SSH_KEY` and then to the usual
`~/.ssh` candidates, so a bench holding its own key still works; `git submodule
update --init` is what puts it there.

`GAUNTLET_TARGET` still names the host when `--target` does not.

## Profiles

| Profile | What it is for |
|---|---|
| `quick.yaml` | mock, no hardware. What `gauntlet verify --run` executes |
| `bench.yaml` | two minutes against the real part, to prove the bench is wired right |
| `standard.yaml` | the beam run: eight hours at one tick every thirty seconds |

## Thresholds

`standard.yaml` gates on almost nothing on purpose — a floor chosen before the
part has ever been measured only encodes a guess. Take a `bench.yaml` run and a
pre-exposure `standard.yaml` run first, then set `pass_criteria` from what the
hardware actually does. The numbers worth setting once a baseline exists are
`min_avg_tx_mbps`, `min_avg_rx_mbps`, `max_udp_loss_pct` and, if the part is
expected to survive the planned dose, `require_otp_stable` and
`require_link_at_end`.

## OTP is read, never written

OTP is one-time-programmable. The suite reads it with `ethtool -e` and compares
a hash against the image captured at setup. Nothing in this suite issues
`ethtool -E`, and nothing should be added that does: a write would be
unrecoverable and would destroy the part it is meant to characterise.

## Reading a finished run

- `first_failure_tick` — the tick the part stopped producing a measurement.
  With the tick period and the dose rate, that is the failure dose. `-1` means
  it never failed.
- `otp_changes` / `register_changes` — how many ticks read back an image
  different from the pre-exposure one.
- the `anomalies.*` rows — one per probe, so link, counters, PCIe, OTP and
  kernel problems are separable at a glance.
- `lan7430-baseline.json` in the run directory — the full pre-exposure state,
  including the OTP hex, so a changed image can be diffed byte by byte.

Degradation, link loss and recovery are all recorded rather than fatal: the
session runs to its configured duration whatever the part does.

# Devcontainer

Python 3.12 on Debian bookworm, with `uv` and the project installed editable.
Built from `Dockerfile` rather than the stock image, to add the system packages
in `../dependencies.txt`, the `claude` and `codex` agents, and the `dev` user.

```bash
make dev          # start the container and open a shell in it
make dev-stop     # stop and remove it
make dev-status   # show its state
```

`make dev` requires the devcontainer CLI:

```bash
npm install -g @devcontainers/cli
```

VS Code and other editors read `devcontainer.json` directly and do not need the
CLI or the make targets.

`REBUILD=1 make dev` discards the existing container and builds a fresh one.

## The dev user

Everything runs as `dev`. The repository is bind-mounted, so anything written
inside — `.venv`, `build/`, `output/`, scaffolded suites — lands on the host
owned by whichever uid wrote it. `dev` is the base image's own non-root user
renamed, keeping its `nvm` and `pipx` group memberships and its sudoers entry,
with uid and gid tracking the caller's. One owner on both sides, and no `sudo`
needed to clean up afterwards.

The uid and gid default to 1000. On a host that is not 1000:1000, either pass
`USER_UID` and `USER_GID` as build args or let the devcontainer CLI's
`updateRemoteUserUID` make the same adjustment when the container is created.

## Git

`~/.gitconfig` and `~/.config/git` are mounted onto `dev`'s home, so identity,
aliases and global excludes are the host's and commits made inside the
container are attributed the same way. Both are writable, so
`git config --global` in here reaches the file the host reads.

`git-lfs` is installed because a host config declaring the lfs filter with
`required = true` fails every checkout when the binary is absent.

A single-file bind mount follows the inode, so if something on the host
replaces `~/.gitconfig` wholesale rather than editing it in place, the
container keeps seeing the old contents until it is restarted.

Credentials are deliberately not mounted. Pushing over SSH needs a key:

```jsonc
"mounts": [
  "source=${localEnv:HOME}/.ssh,target=/home/dev/.ssh,type=bind,readonly"
]
```

## Agents

`claude` and `codex` are installed globally under `/opt/npm-global`, which
belongs to `dev` so both can apply their own updates without `sudo`. Node comes
from NodeSource because bookworm's is older than Claude Code requires.

`~/.claude` and `~/.codex` are mounted from the host, so neither has to be
signed in again inside the container.

Pin either agent for a reproducible image with the `CLAUDE_CODE_VERSION` and
`CODEX_VERSION` build args; both default to `latest`.

## Contents

`postCreateCommand` runs `make setup`, so the container comes up with `.venv`
populated and both packages installed. Port 7100 is forwarded for the app and
7101 is reserved for the frontend dev server.

System packages come from `../dependencies.txt`, which lives at the repository
root because it describes a development machine rather than this container — a
bare host developing on the project needs the same set. The server image needs
a shorter one and keeps it in `../docker/dependencies.txt`.

The host's X11 socket is mounted at `/tmp/.X11-unix` and `DISPLAY` is passed
through, so `make app-dev` opens a window on the host's display. On a host
without X11 both are empty; `xvfb-run make app-dev` runs the app headless
there, which is also how it is exercised in CI.

`suites/*/suite.yaml` is bound to the schema the running app serves at
`/api/schemas/suite`, so the YAML extension validates a manifest as it is
edited while `make run` is up.

## Hardware access

The host's `/dev` is bind-mounted, so a USB instrument on the bench is
reachable from in here. The bind is what makes hotplug work: `--device`
passthrough names one file and fails when it is absent, where a directory bind
carries the host's devtmpfs itself, and an instrument plugged in — or replugged
onto a different number — appears without restarting the container.

Being visible is not being usable. `runArgs` permits three device majors and
Docker denies every other, so the host's disks are listed in `/dev` and cannot
be opened:

| Major | Nodes | What uses it |
|---|---|---|
| 166 | `ttyACM*` | USB CDC-ACM serial |
| 188 | `ttyUSB*` | USB serial bridges, the PSU's CH340 among them |
| 189 | `bus/usb` | raw USB, which the DATAQ DAQ is driven over |

`dev` joins `dialout` and `plugdev` in the image. A bind-mounted node carries
the host's numeric gid rather than a name, and Debian and Ubuntu both number
those 20 and 46, which this image matches. A host numbering them differently
needs its own gid added instead:

```jsonc
"runArgs": ["--group-add=<gid>"]
```

The DAQ additionally needs the udev rule in `../system/`, without which its
usbfs node stays `root:root` and `dev` can read its descriptors and nothing
else. Install it on the host, not in here — see the header of the file.

Diagnose a missing instrument on the bus before suspecting the container.
`lsusb` and `ls -l /dev/ttyUSB* /dev/bus/usb/*/*` say the same thing on both
sides of the bind; an instrument absent from the host's `lsusb` is cabling.

Suites reaching a unit over a network transport — `can_bus` (socketcan),
`piezo` (MQTT broker on the unit) — still run against their `mock` profiles
here, since neither is a character device that can be passed through. The
client tools they are inspected with (`candump`, `cansend`, `mosquitto_pub`,
`ip`) are installed. For bench work on those:

```jsonc
"runArgs": [
  "--network=host",               // can_bus, and reaching a unit under test
  "--cap-add=NET_ADMIN"           // configuring a socketcan interface
]
```

SSH-based suites (`ssd`, `ethernet`, `hardware_trigger`) additionally need a
key. Mount one rather than copying it in:

```jsonc
"mounts": [
  "source=${localEnv:HOME}/.ssh,target=/home/dev/.ssh,type=bind,readonly"
]
```

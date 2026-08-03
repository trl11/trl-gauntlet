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
root because it describes what the suites' transports need, not what the
container needs — a bare host running the same suites needs the same set.

`suites/*/suite.yaml` is bound to the schema the running app serves at
`/api/schemas/suite`, so the YAML extension validates a manifest as it is
edited while `make run` is up.

## Hardware access

The container has no device passthrough. Suites that reach real hardware —
`rs422` (serial), `can_bus` (socketcan), `piezo` (MQTT broker on the unit) —
run against their `mock` profiles here. The client tools those transports are
inspected with (`candump`, `cansend`, `mosquitto_pub`, `ip`) are installed.

For bench work add the devices the suite needs to `devcontainer.json`:

```jsonc
"runArgs": [
  "--device=/dev/ttyUSB0",        // rs422
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

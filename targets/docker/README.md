# Server image

Gauntlet as a server: the API, the built frontend and every built-in campaign,
in one image. No window and no instruments — what differs from the desktop
bundle is only who starts the server and where the hardware is.

| Command | Does |
|---|---|
| `make docker-build` | Build `gauntlet:latest` |
| `make docker-run` | Run it, publishing the port |
| `make docker-save` | Write the image to `dist/` as a loadable tarball |
| `make docker-stop` | Stop and remove the container |

Or from this directory, `docker compose up`.

## The build context is the repository

The image needs both packages, the frontend and the ui-kit submodule, so the
context is the repository root and not this directory. `docker build -f` and
compose's `context: ../..` both say so.

`.dockerignore` therefore stays at the repository root: Docker reads it from
the root of the context, not from beside the Dockerfile. It excludes
`targets/app/` and `dist/`, whose CPython runtime and installers would
otherwise put the best part of a gigabyte through the context on every build.

The frontend is built in a Node stage and copied into the wheel's `web_dist`
before pip runs, because the wheel carries it as package data and the image has
no other copy of it.

## What it runs as

Not root: the image adds a `gauntlet` user with uid 1000 and switches to it.
`--host 0.0.0.0`, because the container's own loopback would be reachable by
nothing.

| Path | Is |
|---|---|
| `/data` | A volume. Runs, the run index and `config.yaml` — the only state. |
| `/opt/gauntlet/campaigns` | The built-in campaigns, read-only to the server. |
| `/campaigns` | A mount point for campaigns of your own. |
| `/suites` | A mount point for a suite belonging to no campaign. |

Discovery skips a root with nothing in it, so both mount points are optional
and empty by default. A campaign carries its own suites, so one mounted at
`/campaigns` brings them with it and needs no second mount.

`GAUNTLET_SUITES` and `GAUNTLET_CAMPAIGNS` point compose at directories
elsewhere. The daemon resolves a bind source against its own host, so from
inside the devcontainer `GAUNTLET_HOST_WORKSPACE` supplies the host's path to
the checkout.

## Ports

`EXPOSE 7100`, and `make docker-run` publishes `DOCKER_PORT` to it. In the
devcontainer that is 7102 rather than 7100, because the socket the devcontainer
reaches belongs to the host, which already publishes 7100 and 7101 for the
devcontainer itself. Both numbers are declared in
[`../../common.mk`](../../common.mk).

## Hardware

Nothing here reaches an instrument. The suites' transports are installed —
[`dependencies.txt`](dependencies.txt) is the shorter of the two package
lists — but a device has to be passed in, and `docker-compose.yml` carries the
`devices`, `network_mode` and `cap_add` lines commented out for when one is.
SocketCAN needs the host's network namespace, since a `can0` interface cannot
be passed through the way a character device can.

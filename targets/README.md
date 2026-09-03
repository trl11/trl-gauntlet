# Targets

The three ways this ships. One application, packaged for three kinds of
machine.

| Directory | Builds | For |
|---|---|---|
| [`app/`](app/) | `gauntlet-<version>.AppImage` and `gauntlet-<version>.deb` | A workstation or a bench someone sits at. A window, and a backend behind it. |
| [`docker/`](docker/) | `gauntlet-<version>-image.tar.gz` | A server. No instruments, no window. |
| [`service/`](service/) | `gauntlet-rig-<version>.deb` | A bench left running as a rig, serving the lab. No display, nobody logged in. |

Each owns the Makefile that builds it, and the top-level targets only
delegate — `make -C targets/app build` and `make app-build` are the same
thing. `make build` runs all three, plus the two wheels.

## What they have in common

Every artifact lands in `dist/`, and every path, port and the version they are
named for come from [`../common.mk`](../common.mk), which all three include.
Nothing here declares a directory of its own.

All three carry the same two things: the `gauntlet` wheel, which has the built
frontend inside it as package data, and every campaign, which is where every
suite lives. So the frontend is built before any of them, and a suite added to
a campaign reaches all three without any of them naming it.

None of them ships a bare `suites/` of its own, because every suite that ships
belongs to a campaign. Each still names a suite root, at a path that is empty
or absent, so that a bundle started in a directory that happens to hold a
`suites/` does not discover what is in it.

## What differs

`app/` and `service/` ship a relocatable CPython with the wheels installed
into it, because a suite is a separate process that imports `gauntlet_sdk` and
`python` in a manifest has to resolve to something. `service/` uses the one
`app/` builds rather than building a second, so a rig and a desktop bundle run
the same interpreter with the same packages in it.

`docker/` needs none of that: the image has a Python, and `pip install` is
the whole of it.

## Where the state is

Nowhere here. Runs, the run index, `config.yaml` and the datasheets live in
the data directory, which is `~/.config/gauntlet` for the app and the rig
service and the `/data` volume for the image. A rebuild replaces the bundle
and leaves all of it alone.

# Desktop app

An Electron window and the backend behind it, packaged as an AppImage and a
deb. `make app-build` builds both into `dist/`.

The window is a `BrowserWindow` pointed at the backend over http, not at files
on disk, so what it shows is the same bundle a browser gets. `main.ts` asks the
kernel for a free port rather than assuming 7100, and starts the backend on it
detached, so that it leads its own process group and a suite it spawned goes
with it. Packaged, it points the backend at the campaigns inside the bundle and
at Electron's own user-data directory.

| Command            | Does                                                                                                           |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| `make app-dev`     | Run the shell against the checkout: the virtualenv's `gauntlet`, the checkout's campaigns, `output/` for state |
| `make app-runtime` | Fetch CPython and install both packages into `runtime/`                                                        |
| `make app-build`   | Both installers, smoke-tested, into `dist/`                                                                    |
| `make app-smoke`   | Run what is built with `runtime/` moved aside                                                                  |
| `make app-check`   | prettier, eslint, `tsc --noEmit`                                                                               |

## The runtime

`runtime/` is a relocatable CPython from python-build-standalone with
`gauntlet-sdk[remote]` and `gauntlet` installed into it. Not a frozen binary: a
suite is a separate process that imports `gauntlet_sdk`, and `python` in a
manifest resolves to the interpreter Gauntlet runs under, so an interpreter has
to sit beside it. `PYTHON_BUILD_RELEASE` and `PYTHON_BUILD_VERSION` in the
Makefile are bumped together — the release date is part of the filename.

pip runs with `-s`. This interpreter shares its minor version with the build
host's and would otherwise read `~/.local/lib/pythonX.Y/site-packages`, count
what it finds there as already installed, and put nothing in the bundle. The
`remote` extra is not optional here even though it is to the SDK: the bundle
carries campaigns whose suites reach their unit over SSH, and one that ships
those without paramiko fails at setup with an instruction no operator can act
on.

`make prune` then throws out what the install_only tarball carries to develop
against and not to ship: the test suite, the static library, the headers and
Tk. It also strips libpython's debug symbols, which are five sixths of it.

## The smoke test

`scripts/smoke.sh` runs the packaged app with `runtime/` moved out of the way,
and `make app-build` will not copy an installer into `dist/` until it passes.

Anything in the bundle still pointing at the tree that built it works on the
build host and nowhere else, because the build host is the one machine where
that path exists. An absolute shebang in a pip console script is exactly that,
and it shipped once already. Parking the runtime is what makes the difference
visible here rather than on the machine the app is installed on.

Which is why the backend is started as `python3 -m gauntlet` by path, never
through the `gauntlet` console script beside it. pip writes that script a
shebang naming the interpreter as it stood when the runtime was built, so
anywhere else it fails to exec with `ENOENT` — reported against a file that
plainly exists, because it is the interpreter that is missing and not the
script. The interpreter is the one thing python-build-standalone makes
relocatable, so it is the one thing invoked by path.

## What the bundle carries

`electron-builder.json` names `runtime/` and `../../campaigns` as
`extraResources`, and every path in that file resolves against this directory.
The campaigns bring every suite with them, which is why nothing names a suite
directory.

Output goes to `build/app/<version>/`, which holds the unpacked tree as well as
the two installers; only the installers are copied into `dist/`. They are named
for `package.json`'s version, which `make version-check` holds equal to
`VERSION`.

## What ships beside them

`make host-setup` installs the loose files from
[`../service/`](../service/) into `dist/` — the setup scripts, the udev rules,
both units, the landing page and its banner, and `README.txt`. An installer
alone cannot install a udev rule or make itself come back after a reboot, so
those ship next to it for a bench that installs by hand.
[`../service/`](../service/) packages the same files as one deb for a bench
that would rather not.

# Rig service

Everything a bench needs to serve Gauntlet to the lab all the time, packaged
as one deb. `make service-build` writes `gauntlet-rig-<version>.deb` into
`dist/`.

[`../../docs/deploying.md`](../../docs/deploying.md) is the operational side:
installing it, standing a new rig up, and checking on one. This page is about
the directory.

`README.txt` is not this file. It is the note that ships in `dist/` for
whoever unpacks a release with no checkout to read, and it is written for an
operator rather than for someone changing this.

## What is here

| File | Is |
|---|---|
| `Makefile` | The package build |
| `package/control` | The deb's metadata, with `@VERSION@` and `@SIZE@` filled in at build time |
| `package/postinst` | What dpkg runs after unpacking |
| `gauntlet.service` | The backend's systemd user unit |
| `serve-gauntlet.sh` | What that unit runs |
| `homepage/` | The landing page, its banner, the server for it and its unit |
| `setup-host.sh` | The udev rules and groups, for a bench installing by hand |
| `setup-bench.sh` | That plus the packages a fresh bench needs |
| `install-service.sh` | Both units, for a bench that has the AppImage rather than the package |
| `README.txt` | The release note, described above |
| `99-gauntlet-instruments.rules` | usbfs nodes to a group the operator is in |
| `60-gauntlet-unprivileged-ports.conf` | Lets a user unit bind port 80 |

Everything but the Makefile, `package/` and this file also ships loose in
`dist/`, put there by `make -C ../app host-setup`. The deb is one way to
install them and copying them to a bench is the other.

## What the package installs

| Path | Holds |
|---|---|
| `/opt/gauntlet/runtime` | CPython with both wheels in it, built by [`../app`](../app) |
| `/opt/gauntlet/campaigns` | Every campaign, and so every suite |
| `/opt/gauntlet/serve-gauntlet.sh` | The backend's entry point |
| `/opt/gauntlet/homepage` | The page, the banner and the server for it |
| `/usr/lib/systemd/user` | Both units |
| `/usr/lib/udev/rules.d` | The instrument rules |
| `/usr/lib/sysctl.d` | The unprivileged-port setting |

`postinst` does the root half of a bench setup that a bundle could never do for
itself: reloading udev and applying it to what is already plugged in, applying
the sysctl, and adding whoever ran the install to `dialout` and `video`.

It starts nothing. Both units are systemd **user** units, because the udev
rules grant the instruments to the operator's groups rather than to root's, and
dpkg does not know which account the operator's is. So the two commands that
start them are printed rather than run.

## The placeholder in the units

Both units carry `ExecStart=@SERVE@`, because where a bundle lands is known
only to whoever put it there. The Makefile rewrites it at packaging time for
`/opt/gauntlet`; `install-service.sh` rewrites it on the bench for an unpacked
AppImage. One unit file, two installs, and no second copy to disagree with the
first.

Neither names a host or port. The application defaults to every interface on
7100 and `config.yaml` in the data directory is where that changes, so a unit
repeating it would be a second place to be wrong.

## The runtime is the app's

`make build` here runs `make -C ../app runtime` rather than building a second
one, so a rig and a desktop bundle run the same interpreter with the same
packages in it. That target always rebuilds, so a top-level `make build` pays
for it twice. The alternative is packaging whatever runtime the last build
happened to leave behind.

## Building it

`dpkg-deb --root-owner-group`, so no fakeroot: everything in the package is
root's on the far side anyway, and it is one fewer thing a build host has to
have. The staged tree is left under `build/service/<version>/` until the next
build or a `make clean`, so `dpkg-deb -c` on the result and a look at that
directory answer most questions about what went in.

The architecture is `amd64` because the CPython the runtime comes from is
`x86_64-unknown-linux-gnu`. Changing `PYTHON_BUILD_TARGET` in
[`../app/Makefile`](../app/Makefile) means changing it here too.

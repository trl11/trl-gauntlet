# Deploying to a bench

A bench that only runs tests now and then wants nothing more than the AppImage
and [`setup-bench.sh`](../system/setup-bench.sh); [the release
README](../system/README.txt) covers that, and it is what an operator who has
no checkout reads.

This is about the other kind: a bench left running as a rig, serving Gauntlet
to the lab all the time.

## What runs there

The desktop bundle is a window and a backend for it. A rig wants only the
backend, so [`serve-gauntlet.sh`](../system/serve-gauntlet.sh) unpacks the
AppImage once and runs the Python inside it directly. Electron never starts,
which is what lets the rig serve with no display, no graphical session and
nobody logged in.

[`gauntlet.service`](../system/gauntlet.service) is a systemd **user** unit,
not a system one. Two reasons, and both matter:

- The udev rules grant the instruments to a group, and it is the operator's
  account that is in it. A system unit would run as root or need a user
  declared in it; this one already runs as the right account.
- Installing it needs no root, so a deploy needs no password. Only the udev
  rules do, which is why they are the one step
  [`deploy-bench.sh`](../scripts/deploy-bench.sh) prints rather than performs.

`install-service.sh` enables lingering for the account, which is what makes
systemd start the unit at boot instead of at the next login. Without it a rig
that reboots unattended comes back with nothing serving.

The unit names no host or port. The application already defaults to every
interface on 7100, and `config.yaml` in the data directory is the one place
that changes — a unit repeating it would be a second place to disagree.

## Deploying

```
make build
make deploy BENCH=trl@blinky
```

`deploy` sends what is in `dist/` and does not build it. A deploy is meant to
put the release that was built and checked on a bench, not whatever the working
tree happens to hold at the time.

It copies the AppImage and the scripts beside it — not the deb, the wheels or
the server image, which are for other ways of installing — and then runs
`install-service.sh` on the far side, which restarts the service on the new
bundle. Updating a bench is the same command as deploying to it for the first
time.

`BENCH_DIR` names the directory on the bench, and defaults to `gauntlet` in the
operator's home.

## Where the state is

`serve-gauntlet.sh` points the backend at `~/.config/gauntlet`, which is where
the packaged desktop app puts the same things. So a machine that has run the
app and then becomes a rig keeps its run history, and someone who opens the app
on a rig sees the runs the service recorded.

The unpacked bundle is not state. It lives under `~/.cache/gauntlet`, keyed by
the AppImage's name and modification time, and the ones it replaces are deleted
the next time a new bundle is unpacked.

## Checking on one

```
systemctl --user status gauntlet.service
journalctl --user -u gauntlet.service -f
```

Both without `sudo`, and both as the operator's account — a user unit is
invisible to `systemctl` run as root.

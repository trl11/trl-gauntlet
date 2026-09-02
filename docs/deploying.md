# Deploying to a bench

A bench that only runs tests now and then wants nothing more than the AppImage
and [`setup-bench.sh`](../targets/service/setup-bench.sh); [the release
README](../targets/service/README.txt) covers that, and it is what an operator who has
no checkout reads.

This is about the other kind: a bench left running as a rig, serving Gauntlet
to the lab all the time. Everything such a bench needs is in
[`targets/service/`](../targets/service/), and `make service-build` packages the
lot as one deb.

## The package

```
sudo apt install ./gauntlet-rig-0.1.0.deb
loginctl enable-linger $USER
systemctl --user enable --now gauntlet.service gauntlet-homepage.service
```

The deb carries the application, the interpreter it runs under, every campaign
and so every suite, both units, the landing page, the udev rules and the
sysctl. It installs into `/opt/gauntlet`, and its `postinst` does the root half
of the setup: reloading udev, applying the sysctl, and adding whoever ran the
install to `dialout` and `video`.

It does not start anything. The units are user units and have to run as the
operator, which is not something dpkg knows, so the two commands above are
printed rather than performed. Lingering is what starts them at boot rather
than at the next login.

That is the whole install on a rig that has never been deployed to. The rest of
this page is the other route, which sends the desktop AppImage over SSH and is
what `make deploy` and `make deploy-rig` do.

On a bench that already ran `install-service.sh`, clear the units it wrote
before installing the package. They are in the operator's own directory, which
systemd searches first, so they would shadow the package's and go on serving
the old AppImage:

```
systemctl --user disable --now gauntlet.service gauntlet-homepage.service
rm ~/.config/systemd/user/gauntlet.service ~/.config/systemd/user/gauntlet-homepage.service
systemctl --user daemon-reload
```

## What runs there

The desktop bundle is a window and a backend for it. A rig wants only the
backend, so [`serve-gauntlet.sh`](../targets/service/serve-gauntlet.sh) runs the Python in
the bundle directly. Electron never starts, which is what lets the rig serve
with no display, no graphical session and nobody logged in.

A bundle is a directory holding `runtime/` and `campaigns/`, and it reaches the
bench one of two ways. The deb installs one at `/opt/gauntlet`, and the script
goes inside it, so there is nothing to unpack. An AppImage carries one instead,
and the script unpacks it once into `~/.cache/gauntlet` and serves it from
there.

[`gauntlet.service`](../targets/service/gauntlet.service) is a systemd **user** unit,
not a system one. Two reasons, and both matter:

- The udev rules grant the instruments to a group, and it is the operator's
  account that is in it. A system unit would run as root or need a user
  declared in it; this one already runs as the right account.
- Installing it needs no root, so a deploy needs no password. Only the udev
  rules do, which is why they are the one step
  [`deploy-bench.sh`](../tools/deploy/deploy-bench.sh) prints rather than performs.

`install-service.sh` enables lingering for the account, which is what makes
systemd start the unit at boot instead of at the next login. Without it a rig
that reboots unattended comes back with nothing serving.

## The landing page

A rig serves Gauntlet on 7100, which is a number someone has to be told. So a
second and separate service answers port 80: [`serve-homepage.py`](../targets/service/homepage/serve-homepage.py)
under [`gauntlet-homepage.service`](../targets/service/homepage/gauntlet-homepage.service), rendering
[`homepage.html`](../targets/service/homepage/homepage.html). Going to the bench's address is
then enough, and the page links on to Gauntlet from there.

It is not part of Gauntlet and holds nothing of its own. `/api/` is proxied
through to Gauntlet on localhost, so `host_stats` stays the one implementation
of what the bench is doing and the page cannot disagree with the application
about it. Proxying rather than fetching 7100 from the browser is also what
keeps the page same-origin, so Gauntlet needs no CORS for it. The page renders
when Gauntlet is down, reporting that, because a broken bench is when someone
is most likely to be looking at it.

Nothing names a host. Every link is built from `location.hostname`, for the
same reason the unit names no host or port: a rig that is renamed or
readdressed needs no edit.

Binding port 80 is the one thing an ordinary account cannot do, and a user unit
can be given neither a capability nor a redirect. So
[`60-gauntlet-unprivileged-ports.conf`](../targets/service/60-gauntlet-unprivileged-ports.conf)
lowers `net.ipv4.ip_unprivileged_port_start` to 80, installed by `setup-host.sh`
alongside the udev rules — the same one root step, rather than a second one.
Every port from 80 up becomes bindable by any local user, which on a
single-operator bench is nobody new.

## Powering a rig down

The System page has Reboot and Shut down, so a bench needs neither an SSH
login nor its power switch. Gauntlet runs `systemctl` on the host it is served
from; the request is refused while a run is in flight.

logind allows that without a password only for a user with an active local
session, and a rig serves from a lingering user manager which has none. So
[`50-gauntlet-power.rules`](../targets/service/polkit/50-gauntlet-power.rules)
grants it to the `dialout` group — the one that already owns the instruments
and runs the service — and `setup-host.sh` installs it alongside the udev
rules. Without it the button answers "Interactive authentication required" and
names the script to run.

`systemctl poweroff --dry-run` does not test this. A dry run never asks
polkit, so it succeeds on a host where the real call is refused.

## Datasheets

The page lists whatever is in `datasheets/` under the data directory, and
serves it:

```
scp datasheet.pdf trl@blinky:~/.config/gauntlet/datasheets/
```

There is no upload. It is a directory, so it is populated the way a directory
is, and it lives in the data directory rather than the bundle, so a redeploy
leaves it alone for the same reason it leaves run history alone. Only `.csv`,
`.md`, `.pdf`, `.png` and `.txt` are listed or served, and a symlink pointing
out of the directory is neither.

The unit names no host or port. The application already defaults to every
interface on 7100, and `config.yaml` in the data directory is the one place
that changes — a unit repeating it would be a second place to disagree.

## Standing a new rig up

```
make build
make deploy-rig RIG_IP=192.168.10.160
```

One command for a machine that has never been deployed to. `RIG_USER` defaults
to `trl` and can be overridden.

A deploy alone is not enough the first time, because four of the things a fresh
host needs are root's to do and a bundle cannot do them for itself.
[`deploy-rig.sh`](../tools/deploy/deploy-rig.sh) does them in the one order that
works, delegating rather than repeating: it turns lingering on first, because
`install-service.sh` treats its own failure to do so as fatal and on a fresh
account that call wants a password; then runs `deploy-bench.sh` unchanged; then
`setup-bench.sh` as root for the packages, the rules, the groups and the
sysctl; then restarts the account's systemd user manager.

That last step is the one worth understanding. A process keeps the groups it
started with, so the services the deploy just started are running without the
`dialout` and `video` membership that `setup-bench.sh` granted a moment later.
Restarting the units does not fix it — they inherit the manager. Restarting the
manager does, and it is also what lets the landing page finally bind port 80.

**The landing page failing to bind during the deploy step is expected**, and
the script says so as it happens. The sysctl that allows it is installed in the
step after.

`sudo` on the rig will ask for a password, two or three times. There is no way
around that: the udev rules, the groups and the sysctl all need root there,
which is the same reason `deploy` prints the rules command rather than running
it.

The datasheets are copied at the end, from `docs/datasheets/`. They live in the
data directory rather than the bundle, so nothing else carries them.

Running it again on a rig that is already up is safe: every step it delegates
to checks before it acts.

## Updating a bench

```
make build
make deploy BENCH=trl@blinky
```

`deploy` sends what is in `dist/` and does not build it. A deploy is meant to
put the release that was built and checked on a bench, not whatever the working
tree happens to hold at the time.

It copies the AppImage and the scripts beside it — not the two debs, the wheels
or the server image, which are for other ways of installing — and then runs
`install-service.sh` on the far side, which restarts the services on the new
bundle. This is the command for a bench that has been set up already;
`deploy-rig` is the one for a bench that has not.

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
systemctl --user status gauntlet.service gauntlet-homepage.service
journalctl --user -u gauntlet.service -u gauntlet-homepage.service -f
```

Both without `sudo`, and both as the operator's account — a user unit is
invisible to `systemctl` run as root.

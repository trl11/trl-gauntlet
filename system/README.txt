Gauntlet
========

Two ways to install, and one setup step that comes before either of them if
this machine has instruments plugged into it. A bench that should serve
Gauntlet all the time has one step after them as well.


Set the host up first
---------------------

    sudo ./setup-bench.sh

Run this once per machine. It is everything a fresh bench needs:

  * libfuse2, which the AppImage mounts itself through. Without it the bundle
    will not start on its own.
  * the instrument udev rules and your `dialout` and `video` membership, by
    running setup-host.sh for you.
  * brltty released from the USB serial adapter. brltty ships a udev rule
    claiming CH340 adapters as braille displays, so a bench supply on one gets
    no /dev/ttyUSB node and Gauntlet reports no PSU on the bench.
  * iperf3, which the ethernet and LAN controller suites measure with.

Every step checks before it acts, so running it again on a machine that is
already set up changes nothing and reports what it found.

Log out and back in afterwards. Group membership is read when a session
starts, so the shell you ran the script from still does not have it.

    sudo ./setup-host.sh

The narrower step, if the udev rules are all you want. Gauntlet drives some
instruments over raw USB, and those device nodes are owned by root until a
udev rule says otherwise, so without this the application starts and reports
the instrument as unavailable with a permission error. The script installs the
rules beside it, applies them to whatever is already plugged in, and adds you
to the `dialout` and `video` groups.

A bench supply on a USB serial port needs no rule of its own. The kernel
already creates /dev/ttyUSB* owned by `dialout`, which is why only the raw-USB
instruments have rules here — and why brltty taking the adapter is enough to
hide one.

A camera is the same case. uvcvideo creates /dev/video* owned by `video`, so
it needs the group and no rule. That group is empty on a fresh Ubuntu, which
is why a camera the kernel has detected can still refuse to open: the desktop
session reaches it through an ACL that a service account does not get.

A logic analyzer needs one thing more. The board carries no acquisition
firmware of its own, so Gauntlet writes sigrok's fx2lafw into it over USB --
which needs the rule above, and needs the firmware file, which is not shipped
here because it is GPL and this is not. Install sigrok-firmware-fx2lafw, or
unpack the firmware anywhere and name it as `logic_firmware` in config.yaml.
Until then the analyzer is listed as unavailable and says which file it
wanted. Once the file is there it loads itself: the board drops off the bus
for a second as it restarts, and the scan after that finds it ready.


Testing a unit over the network
-------------------------------

The ethernet and LAN controller suites reach the unit over SSH and measure
with iperf3, so the unit needs `iperf3` and `ethtool` installed and it needs
to accept the key at ~/.ssh/id_ed25519 on this machine. Set GAUNTLET_SSH_KEY
to use a different one.

Those suites also read registers, the OTP image and the kernel log on the
unit, which need root there. Without passwordless sudo on the unit they are
reported as unreadable and the run carries on with the throughput measurement.


Install the application
-----------------------

    gauntlet-<version>.AppImage

Self-contained: no installation, no dependencies, runs from wherever you put
it. Mark it executable first with `chmod +x`. On a host without libfuse2, run
it as `./gauntlet-<version>.AppImage --appimage-extract-and-run`.

    gauntlet-<version>.deb

For Debian and Ubuntu. Installs to /opt and adds a desktop entry:

    sudo apt install ./gauntlet-<version>.deb

Both carry their own Python and every built-in test suite. Nothing else needs
installing.


Leaving a bench running as a rig
-------------------------------

    ./install-service.sh

Run this on a machine that should serve Gauntlet all the time, rather than
only while someone has the application open. It installs a systemd user
service that starts the backend at boot, restarts it if it stops, and keeps it
running when nobody is logged in.

Do not use sudo. The service runs as you, because the udev rules above grant
the instruments to your groups and not to root's.

What it starts is the backend on its own, without the desktop window: the same
application, reached with a browser instead. The script prints the address when
it is done, which is port 7100 on this machine unless config.yaml says
otherwise. Anyone on the lab network can open it.

    systemctl --user status gauntlet.service     is it running
    journalctl --user -u gauntlet.service -f     what it is doing
    systemctl --user stop gauntlet.service       stop it until next boot
    systemctl --user disable --now gauntlet.service   stop it for good

To put a newer release on a bench that already runs the service, copy this
whole directory over the old one and run ./install-service.sh again. It
restarts the service on the new bundle. Run artifacts and history are kept
somewhere else, so they survive.

The desktop application can still be opened on a machine running the service.
Both read the same run history. They do not serve the same port, so what the
window shows is its own backend, not the service's.

install-service.sh also starts a landing page on port 80, so the bench's bare
address reaches something: what the host is doing, a link on to Gauntlet, and
the datasheets below. It is a separate service from Gauntlet and reads nothing
of its own, so it shows what the application shows, and it still renders while
Gauntlet is stopped.

Port 80 is below the range an ordinary account may bind, so this is the part
that needs setup-host.sh to have been run. If the page does not answer, that is
almost always why.

    systemctl --user status homepage.service     is it running
    journalctl --user -u homepage.service -f     what it is doing


Datasheets on the bench
-----------------------

    scp datasheet.pdf this-bench:~/.config/gauntlet/datasheets/

The landing page lists what is in that directory and serves it, so a part's
datasheet sits with the bench that tests it. Create the directory by copying
the first file into it, or let the service create it when it starts.

It takes .csv, .md, .pdf, .png and .txt. Anything else is ignored rather than
served. The directory is beside your run history rather than in the release, so
updating the bench leaves it alone.


Where your data goes
--------------------

Run artifacts, the run history and the configuration file live under
~/.config/gauntlet (the AppImage) or the equivalent for the packaged app.
Uninstalling leaves them alone.


Checking the instruments
------------------------

Open the application and go to Instruments. Anything plugged in and answering
is listed; anything absent is not. Press Scan after plugging something in.

An instrument listed as unavailable with a permission error means the setup
step above has not run on this machine, or that you have not logged out and
back in since it did.

Gauntlet
========

Two ways to install, and one setup step that comes before either of them if
this machine has instruments plugged into it.


Set the host up first
---------------------

    sudo ./setup-host.sh

Run this once per machine. Gauntlet drives some instruments over raw USB, and
those device nodes are owned by root until a udev rule says otherwise, so
without this the application starts and reports the instrument as unavailable
with a permission error. The script installs the rules beside it, applies them
to whatever is already plugged in, and adds you to the `dialout` group.

Log out and back in afterwards. Group membership is read when a session
starts, so the shell you ran the script from still does not have it.

It is safe to run again — it overwrites the same rules and skips a group you
are already in.

A bench supply on a USB serial port needs none of this. The kernel already
creates /dev/ttyUSB* owned by `dialout`, which is why only the raw-USB
instruments have rules here.


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

# The image this suite measures

`pmu3_firmware.hex` is the PMU3 build the parts on this bench are programmed
with. Its source is [trl-pmu-firmware](https://github.com/trl11/trl-pmu-firmware),
the submodule at `extras/trl-pmu-firmware`.

Its version is read from its own bytes: the firmware embeds the same `fw=`
string it answers the console with.

The build is committed rather than built on demand because a rig in a beam
room has no Microchip toolchain and should not need one: this file plus MPLAB
X's `ipecmd` is the whole of what programming a part takes, where building
needs several gigabytes of XC8 and the device pack.

Program a part, and read the version back off the board afterwards:

    tools/bench/pic_flash.py

That is the default because it is what a bench wants. `--build` builds from
the submodule instead and flashes that; `--verify-only` skips the flash and
just asks the board what it is running.

Nothing programs a part during a run. The suite reads the version off the
board on every run and records it beside this image's SHA-256, so a part
running something else is reported rather than measured quietly.

## Replacing it

Build in the submodule and copy over this file, keeping the name. Then reflash
every part on the bench: the suite compares each one against this image and
reports anything else as a mismatch.

    make -C extras/trl-pmu-firmware build
    cp extras/trl-pmu-firmware/dist/linux_cli/production/pmu3_firmware-*.prod.hex \
       campaigns/radiation_tid/suites/tid_pic18f26k83/firmware/pmu3_firmware.hex

A campaign already part-way through an exposure should not have its image
changed. The dose response being measured is that image's on that die, and a
half-irradiated part reflashed mid-campaign has no comparable result.

# alvium_camera — bench setup

What the suite measures and how it decides is in the module docstrings, chiefly
[`suite/runner.py`](suite/runner.py). This file is the part that lives on the
bench rather than in the code: what has to be true before a run will work.

## What it will drive

Any Allied Vision camera. The bench one is USB `1ab2:0001`, an
"ALVIUM 1800 U-2040c", which sends 4512x4512 RGB8 already debayered on the
sensor board.

It is a USB3 Vision device, not a UVC one. The kernel binds no driver to it and
it gets no `/dev/video*` node — descriptors, registers and pixels all reach it
through its one usbfs node — so `gmsl_camera`'s driver cannot see it at all and
this suite exists as the second half of that pair. Set `camera_device` to
`auto` and Gauntlet takes an Allied Vision camera ahead of any capture node, or
set it to the camera's serial to pin one among several.

## Two things have to be installed, and neither is a driver

| Missing | Looks like | Fix |
|---|---|---|
| the udev rule | `/dev/bus/usb/...: not writable` | `make install-udev-rules`, then replug |
| a GenTL transport layer | `no Allied Vision camera is on the USB bus`, with the camera plainly in `lsusb` | install Vimba X and point `GENICAM_GENTL64_PATH` at its `cti` directory |

The transport layer is the one that catches people out. `vmbpy` carries VmbC
and no layer at all, so a bench without one starts up with an empty bus and
reports no cameras rather than failing. The devcontainer installs the USB layer
at `/opt/vimbax/cti` and sets the variable; a bare bench does not.

## Heat, which does not look like heat

The housing is the camera's heatsink and has to be mounted to something. In
free air an Alvium reaches its limit in about twenty minutes, and past it the
firmware shuts the image path down and latches until the camera restarts.

That failure does not announce itself. The camera stays on the bus and still
answers for its serial, its firmware and its temperature, while `Width`,
`PixelFormat` and every acquisition feature report themselves unreadable — so
it reads as a driver fault or a broken camera. The sensor temperature is
recorded beside every still for exactly this reason, and `max_sensor_c` fails
the run before the shutdown does it for you.

Recovery is **Reboot Camera** in the instrument panel, beside the snapshot
controls. Unplugging works too, and is no use to a camera inside a chamber.

## What a still has to be to pass

Each tick takes one, writes it into `frames/`, and judges it four ways. Every
threshold is in the profile.

| Check | Profile field | Catches |
|---|---|---|
| brightness inside a window | `min_mean_luma`, `max_mean_luma` | a dark frame, a saturated one, no picture at all |
| edge detail above a floor | `min_sharpness` | a lens cap, a badly defocused image, a blank raster |
| not identical to the still before | `max_identical_frames` | an image path that has locked up while still answering |
| the camera content with its own temperature | none — the camera's word | the shutdown above, before it happens |
| sensor below a ceiling | `max_sensor_c` | a runaway the camera has not called yet |

The temperature is judged the camera's way first. It reports a status of its
own beside the two readings, and anything but `OK` fails the check whatever the
number says, because that status is what the firmware acts on. `max_sensor_c`
is only the backstop: an unmounted 1800 U sits around 70C while producing
perfectly good frames, so the default is set above that and below the shutdown.

The third is the one worth understanding. A camera that has frozen still hands
over frames on request, and a run that only counted them would pass. A live
sensor varies by at least its own noise between frames, so byte-identical
stills in a row mean the picture stopped changing, not that the scene did.

Unlike the dose fork, nothing is tolerated: one bad still out of five fails the
run. This is the check an operator runs to be told yes or no.

## Profiles

| Profile | For |
|---|---|
| `bench.yaml` | a real camera; five stills at 960px, about eight seconds |
| `mock.yaml` | no camera at all. What `gauntlet verify --run` executes |

`mock.yaml` sets `driver: mock`, which synthesises frames in the suite and
contacts no instrument, so the suite stays runnable — and its artifacts stay
real PNGs — on a machine with no camera and no Gauntlet application installed.

## Reading a finished run

The stills are in `frames/`, one per iteration, zero-padded so the directory
listing is in the order they were taken. Each is named in that iteration's
`metrics.images`, which is what puts them in the run's **Snapshots** tab.

`camera.mean_luma`, `camera.sharpness` and `camera.sensor_c` chart across the
run and are the three series the suite declares as default.

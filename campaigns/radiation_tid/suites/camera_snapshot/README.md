# camera_snapshot — bench setup

What the suite measures and how it decides is in the module docstrings, chiefly
[`suite/runner.py`](suite/runner.py). This file is the part that lives on the
bench rather than in the code: what has to be true before a run will work.

## The camera has to be openable, which is two things and not one

Gauntlet holds the camera and the suite drives it through a granted capability
URL, so what has to work is the application's access to the node, not the
suite's. A camera that is plainly present can still fail to open, and the two
ways it fails want different fixes. The driver names which one it hit:

| Error | Means | Fix |
|---|---|---|
| `EACCES` | the account is not in the `video` group | join it; `id` should list `video` |
| `EPERM` | a container's device cgroup has no rule for char major 81 | add the rule and **rebuild** the container |

`EPERM` is the confusing one, because it happens to root as well — that is what
tells you it is the cgroup rather than the file's mode. Both are already
handled for the devcontainer: `runArgs` carries
`--device-cgroup-rule=c 81:* rmw` and the image joins `video`. Neither takes
effect until the container is rebuilt.

**No udev rule is needed.** Unlike the DATAQ, which is claimed through usbfs,
the kernel's `uvcvideo` driver already owns `/dev/video*` and leaves it
`root:video 0660`, so there is nothing for `system/setup-host.sh` to install.

## What it will drive

Any UVC camera. A GMSL sensor behind a GMSL-to-USB adapter arrives as an
ordinary capture device, and neither the driver nor this suite learns it was
anything else.

The bench camera is USB `2a0b:00cd`, "Leopard Imaging LI-IMX728", which offers
a single mode:

```
YUYV  3840 x 2160  @ 19 fps
```

Set `camera_device` to `auto` and the driver tries each `/dev/video*` in turn
and takes the first that streams a format it can write — which sorts out the
capture node from the metadata node without anyone naming either. Set it to a
node to pin one, or to `""` not to look for a camera at all.

A 4K YUYV frame is 16.6 MB. Snapshots are scaled on the way out, so what lands
in `frames/` is tens of KB rather than megabytes. `max_width` sets the width;
the height follows the aspect ratio.

Scaling is also what the snapshot costs. The YUYV to RGB conversion is pure
Python and dominates the PNG deflate, so the time tracks the output width and
hardly moves with the content: measured on one 3840x2160 frame, 0.12s at 480px
wide, 0.46s at 960, 1.9s at 1920 and 7.7s at full width. `sample_period_s` is
held at or above 1s for a real run because of it. Ask for a wide snapshot and
the period has to grow with it, or the sample loop simply runs late.

## What a snapshot has to be to pass

Each tick takes one still, writes it into `frames/`, and judges it three ways.
All three thresholds are in the profile.

| Check | Profile field | Catches |
|---|---|---|
| brightness inside a window | `min_mean_luma`, `max_mean_luma` | a dark frame, a saturated one, no picture at all |
| edge detail above a floor | `min_sharpness` | a lens cap, a badly defocused image, a blank raster |
| not identical to the frame before | `max_identical_frames` | a pipeline that has locked up while still answering |

The third is the one worth understanding. A camera that has frozen still hands
over frames on request, and a run that only counted them would pass. A live
sensor varies by at least its own noise between frames, so byte-identical
stills in a row mean the picture stopped changing, not that the scene did.
`max_identical_frames` is how many in a row are tolerated before that is called
a fault; it is not zero, because a repeat can happen once without meaning
anything.

The defaults on `profiles/bench.yaml` are set wide enough to pass any lit
scene. **Narrow them once the camera is pointed at whatever the run is really
watching** — a brightness window that spans almost the whole range will not
notice a part that has started to dim.

## Profiles

| Profile | For |
|---|---|
| `bench.yaml` | a real camera; 30s at one still every 2s, scaled to 960px |
| `mock.yaml` | no camera at all. What `gauntlet verify --run` executes |

`mock.yaml` sets `driver: mock`, which synthesises frames in the suite and
contacts no instrument, so the suite stays runnable — and its artifacts stay
real PNGs — on a machine with no camera and no Gauntlet application installed.

## Reading a finished run

The stills are in `frames/`, one per iteration, zero-padded so the directory
listing is in the order they were taken. Each is named in that iteration's
`metrics.images`, which is what puts them in the run's **Snapshots** tab — a
grid that opens one full size and steps through them.

`camera.mean_luma` and `camera.sharpness` chart across the run like any other
reading, and are the two series the suite declares as default. For a dose run
they are the interesting ones: a part that is degrading shows up as a drift in
brightness or a fall in edge detail before it stops producing frames
altogether.

`camera.repeats` counts consecutive identical stills and is normally zero.

## What it does not measure yet

Dropped frame count, corruption count and data rate in Mbps. The V4L2 layer
already carries the frame sequence numbers and byte counts those would be
derived from, but the suite does not report them.

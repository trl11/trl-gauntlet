# tid_alvium — bench setup

What the suite measures and how it decides is in the module docstrings, chiefly
[`suite/runner.py`](suite/runner.py). This file is the part that lives on the
bench rather than in the code: what has to be true before a run will work.

Run the hardware campaign's [`alvium_camera`](../../../hardware/suites/alvium_camera/)
against the camera first. It asks the same questions in eight seconds and is
the cheapest way to find that the bench is not wired the way this suite
assumes.

## Before the beam is on

Three things, and only the third is about radiation.

**The udev rule.** The camera is USB3 Vision, not UVC. It gets no
`/dev/video*` node and the whole of it — descriptors, registers, pixels —
reaches through one usbfs node, which is `root:root` until the rule in
`targets/service/` is installed with `make install-udev-rules`.

**A GenTL transport layer.** `vmbpy` carries VmbC and no layer, so a bench
without one starts up with an empty bus and reports no cameras at all, with the
camera plainly visible in `lsusb`. The devcontainer installs Vimba X's USB
layer at `/opt/vimbax/cti` and points `GENICAM_GENTL64_PATH` at it.

**The housing mounted to something.** The housing is the camera's heatsink. In
free air an Alvium reaches its limit in about twenty minutes.

## Heat counterfeits the failure you are looking for

Past its temperature limit the firmware shuts the image path down. The camera
stays on the bus and still answers for its serial, its firmware and its
temperature, while `Width`, `PixelFormat` and every acquisition feature report
themselves unreadable — which is exactly what a part killed by dose looks like
from the outside.

So the sensor temperature is read on every tick and charted beside the
measurements, and it is read again whenever a still fails, because that is the
one reading a shut-down camera still gives. Look at it before reading a failure
as dose.

The shutdown latches until the camera restarts. The suite reboots the camera
itself once `recovery.after_failures` stills in a row have not arrived, waits
`recovery.settle_s` for it to come back on its new bus address, and takes it
again — up to `recovery.max_resets` times, after which a camera that is not
coming back is left alone and recorded. Unplugging is the only other recovery
and is no use to a camera inside a chamber.

## Pin the exposure before the beam

`standard.yaml` and `continuous.yaml` set `exposure_us`, and they have to.

The camera's own metering compensates for a sensor that is dimming — which is
the change this run exists to record. Left on, `camera.luma_drift` reads flat
through a part that is visibly degrading, and the run measures the camera's
opinion of the scene rather than the scene. The suite warns in the log when
`exposure_us` is zero, and does not stop, because a bench run with metering on
is a reasonable thing to do.

Get the number from a `bench.yaml` run, which leaves metering to the camera:
read the exposure it settles on off the instrument panel, then pin that. The
shipped default of 5000 us is the camera's own boot value and is a black frame
in a room that is not brightly lit — it is a placeholder, not a recommendation.

A pinned exposure lasts as long as the connection. Rebooting the camera
restores its boot defaults, so the suite pins the value again after every
recovery; a session measured half at one exposure and half at another measures
nothing.

## What each tick measures

| Reading | Metric | What it says |
|---|---|---|
| brightness | `camera.mean_luma` | absolute, as much the scene as the part |
| brightness drift | `camera.luma_drift` | change from the baseline — the part |
| edge detail | `camera.sharpness` | absolute |
| edge detail ratio | `camera.detail_ratio` | fraction of the baseline left |
| sensor, mainboard | `camera.sensor_c`, `camera.mainboard_c` | whether heat is the explanation |
| repeats | `camera.repeats` | consecutive byte-identical stills |
| reboots | `camera.resets` | how often the camera had to be restarted |

The first `baseline_frames` stills fix what the drift is measured against.
They are taken under the beam like every other, so the baseline carries the
least dose rather than none — which is the honest thing to compare against
when a pre-exposure run would have been a different session on a different day.

**Absolute brightness and sharpness say little on their own.** They are as much
the lens, the scene and the lighting as the part. The drift is the measurement.

## Anomalies

Recorded in `events.jsonl` and announced in the run log as they happen. None of
them stops the run.

| Kind | Raised when |
|---|---|
| `brightness_drift` | brightness has moved past `max_luma_drift` from the baseline |
| `detail_lost` | edge detail has fallen below `max_sharpness_drop` of the baseline |
| `frozen_frame` | more than `max_identical_frames` byte-identical stills in a row |
| `thermal_status` | the camera calls its own temperature anything but `OK` |
| `sensor_hot` | the sensor is above `max_sensor_c` |
| `snapshot_failed` | a still did not arrive, with the temperature at the time |
| `camera_reset` | the suite rebooted the camera |

## What passes

A still that did not arrive is a failed iteration, and the runner's verdict is
that nothing failed — a suite's own criteria can only add to that, never
overrule it. So a session where the camera missed frames is recorded as
**failed**, and that is the right word for the camera rather than for the
measurement: every frame and every reading up to that point is kept, the run
is never aborted, and the anomalies are what the session is read through.

`pass_criteria.require_measurement` adds the one case the default rule would
otherwise call a pass: a session that took no still at all.

Nothing else is gated. The drift limits raise anomalies, not failures, because
a part that degrades is the result of a dose run rather than a fault in it.
Tighten them once a baseline run exists.

## Profiles

| Profile | For |
|---|---|
| `standard.yaml` | the beam line; eight hours at one still every thirty seconds |
| `continuous.yaml` | the same, with no end of its own — stops when the operator does |
| `bench.yaml` | two minutes against the real camera, to prove the bench before the beam |
| `smoke.yaml` | no hardware. What `gauntlet verify --run` executes |

`smoke.yaml` sets `driver: mock`, which synthesises a camera that degrades the
way a real one does under dose — softening, then drifting, then freezing, then
shutting down and coming back on a reboot — so the analysis, the anomaly rules,
the recovery and the verdict all run over mock data unchanged, on a machine
with no camera and no Gauntlet application installed.

## Reading a finished run

Every still is in `frames/`, zero-padded so the listing is in the order they
were taken, and named in its iteration's `metrics.images`, which is what puts
them in the run's **Snapshots** tab. An eight-hour standard run at 960px is
about a thousand frames and a few hundred MB.

`camera.mean_luma`, `camera.sharpness`, `camera.sensor_c` and
`camera.luma_drift` are the four series the suite charts by default. Read the
drift against the temperature: a fall in edge detail that tracks the sensor
warming is the bench, and one that does not is the part.

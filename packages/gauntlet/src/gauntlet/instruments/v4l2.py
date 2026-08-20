"""Video4Linux2 capture, driven through ioctls rather than a capture library.

A UVC camera is a kernel device with a stable userspace ABI, so the whole
driver is `ioctl` calls against structures laid out to match `videodev2.h`.
That keeps the dependency rule the rest of the application follows: nothing is
installed to read the bench.

The structure sizes are the ABI. Each one is asserted against the size the
kernel encodes into its ioctl number, so a layout that drifts fails on import
here rather than as an unreadable frame much later.

Capture is memory-mapped: buffers are allocated by the driver, mapped once, and
cycled between the driver's queue and this process for the length of the
stream. Frames are dequeued with a select() first, so a camera that stops
producing times out rather than blocking the caller forever.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import mmap
import os
import select
import time
from pathlib import Path
from typing import Any

BUF_TYPE_VIDEO_CAPTURE = 1
BUF_FLAG_ERROR = 0x00000040
CAP_VIDEO_CAPTURE = 0x00000001
CAP_STREAMING = 0x04000000
MEMORY_MMAP = 1

# Long enough to see a buffer that is already waiting, short enough not to wait
# for the next frame to be captured.
_BACKLOG_POLL_S = 0.002

# The formats this module can turn into a file. YUYV is packed luma and
# chroma that has to be converted; MJPEG is already a JPEG and is written out
# untouched.
PIXELFORMAT_MJPG = 0x47504A4D
PIXELFORMAT_YUYV = 0x56595559
SUPPORTED_FORMATS = (PIXELFORMAT_YUYV, PIXELFORMAT_MJPG)

_IOC_WRITE = 1
_IOC_READ = 2


class V4l2Error(RuntimeError):
    """The device refused an ioctl, or produced no frame."""


def _ioc(direction: int, size: int, number: int) -> int:
    """One ioctl request number, encoded the way `asm-generic/ioctl.h` does."""
    return (direction << 30) | (size << 16) | (ord("V") << 8) | number


class _Capability(ctypes.Structure):
    _fields_ = [
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


class _FmtDesc(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("description", ctypes.c_char * 32),
        ("pixelformat", ctypes.c_uint32),
        ("mbus_code", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


class _PixFormat(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixelformat", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("bytesperline", ctypes.c_uint32),
        ("sizeimage", ctypes.c_uint32),
        ("colorspace", ctypes.c_uint32),
        ("priv", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("enc", ctypes.c_uint32),
        ("quantization", ctypes.c_uint32),
        ("xfer_func", ctypes.c_uint32),
    ]


class _Format(ctypes.Structure):
    """`v4l2_format`: a type, then a union padded out to 200 bytes.

    The union holds a pointer in one of its arms, so it is eight-byte aligned
    and starts at offset eight rather than four.
    """

    _fields_ = [
        ("type", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
        ("pix", _PixFormat),
        ("_rest", ctypes.c_uint8 * 152),
    ]


class _RequestBuffers(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("flags", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3),
    ]


class _TimeVal(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_usec", ctypes.c_int64)]


class _TimeCode(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("frames", ctypes.c_uint8),
        ("seconds", ctypes.c_uint8),
        ("minutes", ctypes.c_uint8),
        ("hours", ctypes.c_uint8),
        ("userbits", ctypes.c_uint8 * 4),
    ]


class _Buffer(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("_pad", ctypes.c_uint32),
        ("timestamp", _TimeVal),
        ("timecode", _TimeCode),
        ("sequence", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
        ("length", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("request_fd", ctypes.c_int32),
    ]


VIDIOC_QUERYCAP = _ioc(_IOC_READ, ctypes.sizeof(_Capability), 0)
VIDIOC_ENUM_FMT = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_FmtDesc), 2)
VIDIOC_G_FMT = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_Format), 4)
VIDIOC_S_FMT = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_Format), 5)
VIDIOC_REQBUFS = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_RequestBuffers), 8)
VIDIOC_QUERYBUF = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_Buffer), 9)
VIDIOC_QBUF = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_Buffer), 15)
VIDIOC_DQBUF = _ioc(_IOC_READ | _IOC_WRITE, ctypes.sizeof(_Buffer), 17)
VIDIOC_STREAMON = _ioc(_IOC_WRITE, 4, 18)
VIDIOC_STREAMOFF = _ioc(_IOC_WRITE, 4, 19)

# The kernel encodes the structure size into the request number, so a layout
# that does not match the ABI is caught here rather than by a confusing EINVAL.
for _name, _request, _structure in (
    ("v4l2_capability", VIDIOC_QUERYCAP, _Capability),
    ("v4l2_fmtdesc", VIDIOC_ENUM_FMT, _FmtDesc),
    ("v4l2_format", VIDIOC_G_FMT, _Format),
    ("v4l2_requestbuffers", VIDIOC_REQBUFS, _RequestBuffers),
    ("v4l2_buffer", VIDIOC_QUERYBUF, _Buffer),
):
    _encoded = (_request >> 16) & 0x3FFF
    if _encoded != ctypes.sizeof(_structure):
        raise ImportError(f"{_name} is {ctypes.sizeof(_structure)} bytes, the ABI wants {_encoded}")


def fourcc(value: int) -> str:
    """A pixel format as its four character code, the way `v4l2-ctl` prints it."""
    return "".join(chr((value >> shift) & 0xFF) for shift in (0, 8, 16, 24)).strip()


class Frame:
    """One captured frame and what the driver said about it."""

    def __init__(self, data: bytes, pixelformat: int, width: int, height: int, sequence: int) -> None:
        self.data = data
        self.height = height
        self.pixelformat = pixelformat
        self.sequence = sequence
        self.width = width


def capture_devices() -> list[Path]:
    """Every `/dev/video*` node, in the order the kernel numbered them."""
    nodes = sorted(Path("/dev").glob("video*"), key=lambda path: int(path.name[5:] or 0))
    return [path for path in nodes if path.name[5:].isdigit()]


class V4l2Camera:
    """One capture device, opened for as long as the instrument is registered.

    The device is held open because a UVC node is exclusive: releasing it
    between frames invites another process to take the camera mid-run, and
    reopening costs a full enumeration.
    """

    def __init__(self, path: Path, *, buffer_count: int = 4) -> None:
        self._buffer_count = buffer_count
        self._fd: int | None = None
        self._format: dict[str, Any] = {}
        self._maps: list[mmap.mmap] = []
        self._path = path
        self._streaming = False

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        """Open the node and confirm it can stream video capture."""
        if self._fd is not None:
            return
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            raise V4l2Error(f"{self._path}: {_reason(exc)}") from exc
        self._fd = fd
        try:
            capability = self._querycap()
            caps = capability.device_caps or capability.capabilities
            if not caps & CAP_VIDEO_CAPTURE:
                raise V4l2Error(f"{self._path}: not a video capture device")
            if not caps & CAP_STREAMING:
                raise V4l2Error(f"{self._path}: does not support streaming")
            self._format = self._read_format()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Stop streaming, unmap every buffer and release the node."""
        if self._fd is None:
            return
        # A device that has already gone refuses the ioctls that stop it, and
        # the node still has to be released.
        with contextlib.suppress(V4l2Error):
            self.stop()
        os.close(self._fd)
        self._fd = None
        self._format = {}

    def describe(self) -> dict[str, str]:
        """What the driver calls itself, for the panel and the manifest."""
        capability = self._querycap()
        return {
            "bus_info": capability.bus_info.decode(errors="replace"),
            "card": capability.card.decode(errors="replace"),
            "driver": capability.driver.decode(errors="replace"),
        }

    def formats(self) -> list[dict[str, Any]]:
        """Every pixel format the device offers, whether or not it is usable here."""
        rows = []
        for index in range(32):
            description = _FmtDesc()
            description.index = index
            description.type = BUF_TYPE_VIDEO_CAPTURE
            try:
                self._ioctl(VIDIOC_ENUM_FMT, description)
            except V4l2Error:
                break
            rows.append(
                {
                    "description": description.description.decode(errors="replace"),
                    "fourcc": fourcc(description.pixelformat),
                    "pixelformat": description.pixelformat,
                    "supported": description.pixelformat in SUPPORTED_FORMATS,
                }
            )
        return rows

    def format(self) -> dict[str, Any]:
        """The format capture is currently set to."""
        return dict(self._format)

    def start(self) -> None:
        """Map the driver's buffers, queue them all, and begin streaming."""
        if self._streaming:
            return
        request = _RequestBuffers()
        request.count = self._buffer_count
        request.memory = MEMORY_MMAP
        request.type = BUF_TYPE_VIDEO_CAPTURE
        self._ioctl(VIDIOC_REQBUFS, request)
        if request.count < 1:
            raise V4l2Error(f"{self._path}: the driver granted no buffers")

        for index in range(request.count):
            buffer = self._buffer(index=index)
            self._ioctl(VIDIOC_QUERYBUF, buffer)
            self._maps.append(
                mmap.mmap(
                    self._require_fd(),
                    buffer.length,
                    mmap.MAP_SHARED,
                    mmap.PROT_READ,
                    offset=buffer.offset,
                )
            )
            self._ioctl(VIDIOC_QBUF, buffer)

        self._ioctl(VIDIOC_STREAMON, ctypes.c_uint32(BUF_TYPE_VIDEO_CAPTURE))
        self._streaming = True

    def stop(self) -> None:
        """Stop streaming and release the mapped buffers."""
        if self._streaming:
            self._streaming = False
            self._ioctl(VIDIOC_STREAMOFF, ctypes.c_uint32(BUF_TYPE_VIDEO_CAPTURE))
        for region in self._maps:
            region.close()
        self._maps = []

    def grab(self, *, timeout_s: float = 5.0) -> Frame:
        """Dequeue the next complete frame, copy it, and hand the buffer back.

        The buffer is requeued before the copy is returned so the driver never
        runs short while the caller is busy with the frame.

        A buffer the driver marks as an error, or that carries no bytes, is
        requeued and the next one is waited for. A stream that has just started
        flushes the buffers queued before the sensor produced anything, one
        such frame per buffer, so treating the first as a failure would lose
        every capture taken in the moment after `start()`.
        """
        if not self._streaming:
            raise V4l2Error(f"{self._path}: not streaming")
        fd = self._require_fd()
        deadline = time.monotonic() + timeout_s

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise V4l2Error(f"{self._path}: no frame within {timeout_s:g}s")
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise V4l2Error(f"{self._path}: no frame within {timeout_s:g}s")

            buffer = self._buffer()
            self._ioctl(VIDIOC_DQBUF, buffer)
            # Read out of the structure before requeueing it. VIDIOC_QBUF
            # writes back into the same structure, so anything taken from it
            # afterwards describes the empty buffer just queued rather than
            # the frame that was dequeued.
            flags = buffer.flags
            sequence = buffer.sequence
            try:
                if flags & BUF_FLAG_ERROR:
                    continue
                data = bytes(self._maps[buffer.index][: buffer.bytesused])
            finally:
                self._ioctl(VIDIOC_QBUF, buffer)
            if data:
                return Frame(
                    data=data,
                    height=int(self._format.get("height", 0)),
                    pixelformat=int(self._format.get("pixelformat", 0)),
                    sequence=sequence,
                    width=int(self._format.get("width", 0)),
                )

    def measure_stream(self, *, frames: int = 10, timeout_s: float = 5.0) -> dict[str, float]:
        """Drain the queue for a burst and report what the link sustained.

        The driver fills a buffer only while one is free, so a caller taking a
        frame every second measures its own sampling rate rather than the
        link's: the queue sits full in between and the frames arriving then are
        dropped without ever being counted. Reading back to back for a short
        burst is what makes the frame rate, the data rate and the dropped
        count mean anything.

        Two things are cleared before the timing starts. The queue holds frames
        captured before the call, which come back as fast as they can be handed
        over and would read as an impossibly high rate. And a stream that has
        just started flushes an error frame per buffer, all carrying sequence
        zero, which would put the sequence span far above the frames counted.

        Frames the driver flagged as errors are counted rather than skipped:
        on a radiation bench a corrupt frame is the measurement. Gaps in the
        sequence numbers are frames that never arrived at all, which is a
        different fault and counted separately.
        """
        if not self._streaming:
            raise V4l2Error(f"{self._path}: not streaming")
        if frames < 2:
            raise V4l2Error("a burst needs at least two frames to time")
        fd = self._require_fd()
        deadline = time.monotonic() + timeout_s

        # Drain whatever is already waiting rather than a fixed number of
        # buffers. Between calls the queue sits full and the driver keeps
        # counting the frames it cannot store, so the backlog carries a
        # sequence gap that belongs to the caller's pause, not to the link.
        # Draining until nothing is immediately ready leaves only live frames.
        while self._ready(fd, _BACKLOG_POLL_S):
            if self._recycle(fd, deadline) is None:
                break

        corrupt = 0
        counted = 0
        total_bytes = 0
        first_sequence = -1
        last_sequence = -1
        started = 0.0

        while counted < frames:
            recycled = self._recycle(fd, deadline)
            if recycled is None:
                break
            flags, sequence, bytesused = recycled
            if flags & BUF_FLAG_ERROR or not bytesused:
                corrupt += 1
                continue
            if not started:
                # Timing opens on the first good frame, so the wait for the
                # queue to come round is not charged to the link.
                started = time.monotonic()
                first_sequence = sequence
                counted = 1
                continue
            counted += 1
            last_sequence = sequence
            total_bytes += bytesused

        elapsed = time.monotonic() - started if started else 0.0
        intervals = max(0, counted - 1)
        span = last_sequence - first_sequence if last_sequence >= 0 else 0
        return {
            "bytes": float(total_bytes),
            "corrupt": float(corrupt),
            "dropped": float(max(0, span - intervals)),
            "elapsed_s": round(elapsed, 4),
            "fps": round(intervals / elapsed, 2) if elapsed > 0 else 0.0,
            "frames": float(counted),
            "mbps": round(total_bytes * 8 / elapsed / 1_000_000, 2) if elapsed > 0 else 0.0,
        }

    def _ready(self, fd: int, timeout_s: float) -> bool:
        """Is a buffer waiting right now."""
        ready, _, _ = select.select([fd], [], [], timeout_s)
        return bool(ready)

    def _recycle(self, fd: int, deadline: float) -> tuple[int, int, int] | None:
        """Dequeue one buffer and hand it straight back, reporting what it said.

        Everything needed is read before the requeue: VIDIOC_QBUF writes into
        the same structure, so a field read afterwards describes the empty
        buffer just queued rather than the frame that came out of it.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            return None
        buffer = self._buffer()
        self._ioctl(VIDIOC_DQBUF, buffer)
        flags, sequence, bytesused = buffer.flags, buffer.sequence, buffer.bytesused
        self._ioctl(VIDIOC_QBUF, buffer)
        return flags, sequence, bytesused

    def _buffer(self, *, index: int = 0) -> _Buffer:
        buffer = _Buffer()
        buffer.index = index
        buffer.memory = MEMORY_MMAP
        buffer.type = BUF_TYPE_VIDEO_CAPTURE
        return buffer

    def _ioctl(self, request: int, argument: Any) -> None:
        try:
            fcntl.ioctl(self._require_fd(), request, argument)
        except OSError as exc:
            raise V4l2Error(f"{self._path}: {_reason(exc)}") from exc

    def _querycap(self) -> _Capability:
        capability = _Capability()
        self._ioctl(VIDIOC_QUERYCAP, capability)
        return capability

    def _read_format(self) -> dict[str, Any]:
        form = _Format()
        form.type = BUF_TYPE_VIDEO_CAPTURE
        self._ioctl(VIDIOC_G_FMT, form)
        return {
            "bytesperline": form.pix.bytesperline,
            "fourcc": fourcc(form.pix.pixelformat),
            "height": form.pix.height,
            "pixelformat": form.pix.pixelformat,
            "sizeimage": form.pix.sizeimage,
            "width": form.pix.width,
        }

    def _require_fd(self) -> int:
        if self._fd is None:
            raise V4l2Error(f"{self._path}: not open")
        return self._fd


def _reason(error: OSError) -> str:
    """Why an ioctl or an open failed, in the terms the operator can act on.

    EPERM on a node that is plainly there is the container's device cgroup
    rather than the file's mode, which is the difference between a rule to add
    and a group to join.
    """
    if error.errno == errno.EPERM:
        return "permission denied by the device cgroup (the container needs a rule for char major 81)"
    if error.errno == errno.EACCES:
        return "permission denied (the account is not in the 'video' group)"
    if error.errno == errno.EBUSY:
        return "device busy (another process is streaming from it)"
    if error.errno == errno.ENODEV:
        return "device has gone"
    return os.strerror(error.errno or 0) or str(error)

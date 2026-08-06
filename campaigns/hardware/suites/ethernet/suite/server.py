"""Lab-side TCP endpoint the unit's probe connects back to.

One connection carries one transfer. The client sends an ASCII header line —
``U <nbytes>`` to upload, which the server drains and acknowledges, or
``D <nbytes>`` to download, which the server sources. The server does no
timing; the probe on the unit owns the measurement.
"""

from __future__ import annotations

import contextlib
import socket
import threading

_CHUNK = 256 * 1024
_ACK = b"\x06"


class ThroughputServer:
    """Accepts probe connections until stopped."""

    def __init__(self, port: int, *, backlog: int = 8, connection_timeout_s: float = 60.0) -> None:
        self._port = port
        self._backlog = backlog
        self._connection_timeout_s = connection_timeout_s
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.bound_port = port

    def start(self) -> None:
        """Bind, listen, and accept in the background."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", self._port))
        sock.listen(self._backlog)
        # A short accept timeout keeps the loop responsive to stop().
        sock.settimeout(0.5)
        self._sock = sock
        self.bound_port = sock.getsockname()[1]
        self._thread = threading.Thread(target=self._accept_loop, name="gauntlet-eth-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop accepting and close the listening socket."""
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()

    def _accept_loop(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(conn,), name="gauntlet-eth-conn", daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        with contextlib.suppress(OSError):
            conn.settimeout(self._connection_timeout_s)
            mode, nbytes = self._read_header(conn)
            if mode == "U":
                self._drain(conn, nbytes)
                conn.sendall(_ACK)
            elif mode == "D":
                self._source(conn, nbytes)
        with contextlib.suppress(OSError):
            conn.close()

    @staticmethod
    def _read_header(conn: socket.socket) -> tuple[str, int]:
        """Read the ``<mode> <nbytes>`` header a byte at a time."""
        raw = b""
        while b"\n" not in raw and len(raw) < 64:
            byte = conn.recv(1)
            if not byte:
                break
            raw += byte
        mode, _, count = raw.decode("ascii", errors="replace").strip().partition(" ")
        try:
            return mode, int(count or 0)
        except ValueError:
            return mode, 0

    @staticmethod
    def _drain(conn: socket.socket, nbytes: int) -> None:
        received = 0
        while received < nbytes:
            chunk = conn.recv(min(_CHUNK, nbytes - received))
            if not chunk:
                return
            received += len(chunk)

    @staticmethod
    def _source(conn: socket.socket, nbytes: int) -> None:
        payload = bytes(_CHUNK)
        sent = 0
        while sent < nbytes:
            size = min(_CHUNK, nbytes - sent)
            conn.sendall(payload[:size])
            sent += size

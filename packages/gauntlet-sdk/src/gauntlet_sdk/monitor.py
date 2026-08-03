"""Background sampling of the unit under test.

Runs on its own thread and emits ``live`` records, which do not affect the
iteration counters.
"""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING

from gauntlet_sdk.anomalies import AnomalyLog
from gauntlet_sdk.remote import RemoteError, RemoteTarget, connect, sample_host_metrics
from gauntlet_sdk.reporting.jsonl_sink import JsonlSink

if TYPE_CHECKING:
    import paramiko


class RemoteMonitor:
    """Samples a remote host on a fixed cadence until stopped.

    Holds its own SSH connection, separate from the suite's. A sampling failure
    is recorded as an anomaly and sampling continues.
    """

    def __init__(
        self,
        target: RemoteTarget,
        sink: JsonlSink,
        *,
        period_s: float = 5.0,
        anomalies: AnomalyLog | None = None,
        probe_name: str = "uut_monitor",
    ) -> None:
        self._target = target
        self._sink = sink
        self._period_s = max(float(period_s), 0.5)
        self._anomalies = anomalies
        self._probe_name = probe_name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples = 0
        self._failures = 0

    @property
    def samples(self) -> int:
        return self._samples

    @property
    def failures(self) -> int:
        return self._failures

    def start(self) -> None:
        """Begin sampling in the background."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gauntlet-remote-monitor")
        self._thread.start()

    def stop(self, *, timeout_s: float = 5.0) -> None:
        """Stop sampling and wait briefly for the thread to finish."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    def _loop(self) -> None:
        client: paramiko.SSHClient | None = None
        try:
            while not self._stop.is_set():
                try:
                    if client is None:
                        client = connect(self._target, timeout=5.0)
                    metrics = sample_host_metrics(client)
                    if metrics:
                        self._samples += 1
                        self._sink.write_live({"uut": metrics})
                except (RemoteError, TimeoutError, OSError) as exc:
                    self._failures += 1
                    if client is not None:
                        with contextlib.suppress(Exception):
                            client.close()
                        client = None
                    if self._anomalies is not None:
                        self._anomalies.record(
                            self._probe_name,
                            "sample_failed",
                            detail={"error": f"{type(exc).__name__}: {exc}"},
                        )
                self._stop.wait(self._period_s)
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.close()

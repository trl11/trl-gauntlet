"""Spawns suites, streams their progress, and records how they ended.

One run at a time. The lifecycle is:

1. :meth:`RunSupervisor.start` resolves the suite and profile, checks required
   capabilities, claims ownership of whichever of them are not already owned,
   creates the run directory, and spawns the process.
2. Two reader threads publish stdout and ``metrics.jsonl`` to the run's bus.
3. :meth:`stop` requests a graceful stop; :meth:`abort` terminates.
4. On exit, ``verdict.json`` determines the outcome and the run moves to
   history.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import signal
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gauntlet_sdk.contract import Verdict

from gauntlet.capabilities.registry import CapabilityError, CapabilityRegistry
from gauntlet.suites.discovery import SuiteCatalog, resolve_profile
from gauntlet.supervisor.events import EventBus
from gauntlet.supervisor.launcher import Launch, LaunchError, RunRequest, build_launch
from gauntlet.supervisor.readers import pump_stdout, tail_metrics
from gauntlet.supervisor.recorder import InstrumentRecorder

log = logging.getLogger("gauntlet.supervisor")

TERMINAL_STATUSES = frozenset({"aborted", "error", "failed", "passed"})


class RunConflict(RuntimeError):
    """A run is already in flight."""


class RunRejected(ValueError):
    """The request cannot be turned into a run."""


@dataclass
class RunHandle:
    """Everything known about one run, live or finished."""

    run_id: str
    suite: str
    status: str
    started_at: str
    run_dir: str
    profile: str | None = None
    target: str | None = None
    unit_serial: str | None = None
    ended_at: str | None = None
    duration_s: float | None = None
    verdict: str | None = None
    fail_reason: str | None = None
    argv: list[str] = field(default_factory=list)
    # Instrument instance keys this run records: what its suite requires, plus
    # whatever the operator asked to watch alongside.
    observing: list[str] = field(default_factory=list)
    bus: EventBus | None = None
    process: subprocess.Popen[str] | None = None
    recorder: InstrumentRecorder | None = field(default=None, repr=False)
    # Closes whatever capability this run itself claimed ownership of — a
    # camera the operator already had open is left as it was found. Not
    # serialized: it is machinery, not something a caller reads.
    release_capabilities: Callable[[], None] | None = field(default=None, repr=False)

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the REST API. Excludes live process handles."""
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": self.duration_s,
            "verdict": self.verdict,
            "fail_reason": self.fail_reason,
            "profile": self.profile,
            "target": self.target,
            "unit_serial": self.unit_serial,
            "run_dir": self.run_dir,
            "argv": list(self.argv),
        }


class RunSupervisor:
    """Owns the active run and a bounded window of recent ones."""

    def __init__(
        self,
        *,
        runs_dir: Path,
        user_profiles_dir: Path,
        catalog_provider: Callable[[], SuiteCatalog],
        capabilities: CapabilityRegistry | None = None,
        api_base: str | None = None,
        on_run_started: Callable[[RunHandle], None] | None = None,
        on_run_completed: Callable[[RunHandle], Awaitable[None]] | None = None,
        history_size: int = 32,
    ) -> None:
        self._runs_dir = runs_dir
        self._user_profiles_dir = user_profiles_dir
        self._catalog_provider = catalog_provider
        self._capabilities = capabilities or CapabilityRegistry(api_base=api_base)
        self._api_base = api_base
        self._on_started = on_run_started
        self._on_completed = on_run_completed
        self._history_size = history_size
        self._runs: dict[str, RunHandle] = {}
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._starting: set[asyncio.Task[None]] = set()

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop reader threads will schedule callbacks on."""
        self._loop = loop

    def get(self, run_id: str) -> RunHandle | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[RunHandle]:
        return sorted(self._runs.values(), key=lambda h: h.started_at, reverse=True)

    def active(self) -> RunHandle | None:
        """The in-flight run, if any."""
        return next((h for h in self._runs.values() if not h.finished), None)

    async def start(self, request: RunRequest) -> RunHandle:
        """Validate and create a run, then open its instruments and spawn it.

        Raises when the request itself cannot be honoured — an unknown suite, a
        profile that does not resolve, a capability no instrument on this bench
        provides, an instrument asked for that the bench does not have — and
        such a rejection creates no directory and no history entry.

        Everything that waits on hardware happens after the run exists, and the
        handle is returned without waiting for it. Opening an instrument is
        where a start spends its time: the bench's camera takes seconds to
        reach its frame rate and up to thirty to give up. Holding the request
        open for that left the operator in front of a start form with no sign
        of what was happening, so the run is created first and an instrument
        that will not open ends it as an error, on the run's own page, the way
        a suite that fails to spawn already does.
        """
        async with self._lock:
            if self.active() is not None:
                raise RunConflict("a run is already in progress")

            suite = self._catalog_provider().get(request.suite)
            if suite is None:
                raise RunRejected(f"unknown suite {request.suite!r}")

            profile_path = self._resolve_profile(suite, request)
            try:
                # Only asks whether the bench provides what the suite named,
                # which touches no device. Claiming them is the slow half and
                # waits until the run exists.
                capability_env = self._capabilities.environment(suite.manifest.requires)
            except CapabilityError as exc:
                raise RunRejected(str(exc)) from exc
            observing = self._observed(suite.manifest.requires, request.observe)

            run_id = _new_run_id()
            run_dir = self._runs_dir / suite.key / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                launch = build_launch(
                    suite,
                    request,
                    run_id=run_id,
                    run_dir=run_dir,
                    profile_path=profile_path,
                    api_base=self._api_base,
                    capability_env=capability_env,
                )
            except LaunchError as exc:
                raise RunRejected(str(exc)) from exc

            if profile_path is not None:
                _snapshot_profile(profile_path, run_dir)

            handle = RunHandle(
                run_id=run_id,
                suite=suite.key,
                status="starting",
                started_at=_utc_iso(),
                run_dir=str(run_dir),
                profile=profile_path.name if profile_path else None,
                target=request.target,
                unit_serial=request.unit_serial,
                argv=list(launch.argv),
                observing=observing,
                bus=EventBus(),
            )
            self._runs[run_id] = handle
            self._evict_old()

        task = asyncio.create_task(self._claim_and_spawn(handle, launch, suite.manifest.requires))
        # A task nothing refers to may be collected before it runs.
        self._starting.add(task)
        task.add_done_callback(self._starting.discard)
        return handle

    async def _claim_and_spawn(self, handle: RunHandle, launch: Launch, required: list[str]) -> None:
        """Own the run's instruments, then spawn it.

        Claiming runs on a worker thread because opening a device is blocking,
        and a camera coming up spends seconds of it: inline it would hold up
        every other request, the operator's own progress polling included.

        What it is waiting on is said in the run's log, because this is the one
        stretch of a run with nothing else to show for itself. No process has
        started, so the suite has printed nothing, and a camera that will not
        open takes half a minute to say so.
        """
        wanted = ", ".join(required)
        if required:
            await self._announce(handle, "info", f"opening {wanted}")
        began = time.monotonic()
        try:
            handle.release_capabilities = await asyncio.to_thread(self._capabilities.claim_for_run, required)
        except CapabilityError as exc:
            await self._announce(handle, "error", str(exc))
            await self._fail_to_start(handle, str(exc))
            return
        if required:
            await self._announce(handle, "info", f"{wanted} ready in {time.monotonic() - began:.1f}s")
        # Nothing yields between here and the process existing, so a stop can
        # only have arrived before this or after there is something to signal.
        if handle.status == "aborting":
            reason = "stopped before the suite was started"
            await self._announce(handle, "warning", reason)
            await self._fail_to_start(handle, reason, status="aborted")
            return
        await self._spawn(handle, launch)

    async def _announce(self, handle: RunHandle, level: str, message: str) -> None:
        """Say something in the run's log while there is no suite to say it.

        Written to ``test.log`` as well as published, because the bus is memory
        a finished run eventually loses, and a run that never started is
        exactly the one somebody comes back to read. `pump_stdout` appends, so
        the suite's own output follows these rather than replacing them.
        """
        if handle.bus is not None:
            await handle.bus.publish("log", level=level, message=message)
        with contextlib.suppress(OSError), (Path(handle.run_dir) / "test.log").open("a", encoding="utf-8") as file:
            file.write(message + "\n")

    async def stop(self, run_id: str) -> bool:
        """Request that a run finish early and still produce a verdict.

        Escalates to :meth:`abort` for suites declaring
        ``graceful_stop_signal: NONE``.
        """
        handle = self._runs.get(run_id)
        if handle is None or handle.finished:
            return False
        # Nothing has been spawned, so there is no suite to ask for a verdict
        # and cancelling is all a stop can mean.
        if handle.process is None:
            return await self.abort(run_id)
        suite = self._catalog_provider().get(handle.suite)
        signal_name = suite.manifest.exec.graceful_stop_signal if suite else "SIGUSR1"
        signum = getattr(signal, signal_name, None) if signal_name != "NONE" else None
        if signum is None:
            return await self.abort(run_id)
        try:
            handle.process.send_signal(signum)
        except OSError:
            return False
        handle.status = "stopping"
        if handle.bus is not None:
            await handle.bus.publish("status", status="stopping")
        return True

    async def abort(self, run_id: str, *, sigkill_grace_s: float = 10.0) -> bool:
        """Terminate a run, escalating to SIGKILL after a grace period."""
        handle = self._runs.get(run_id)
        if handle is None or handle.finished:
            return False
        if handle.process is None:
            # Still opening its instruments. Interrupting that part-way would
            # leave a device owned by nobody, so the run is marked instead and
            # the claim gives up as soon as it comes back. Saying so is worth
            # the line: the operator is about to wait out a driver's own
            # timeout, which on the camera is half a minute.
            handle.status = "aborting"
            await self._announce(handle, "warning", "stop requested; waiting for the instruments to finish opening")
            if handle.bus is not None:
                await handle.bus.publish("status", status="aborting")
            return True
        process = handle.process
        try:
            process.send_signal(signal.SIGTERM)
        except OSError:
            return False
        handle.status = "aborting"
        if handle.bus is not None:
            await handle.bus.publish("status", status="aborting")

        def _escalate() -> None:
            try:
                process.wait(timeout=sigkill_grace_s)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    process.kill()

        threading.Thread(target=_escalate, daemon=True, name=f"gauntlet-abort-{run_id}").start()
        return True

    async def _spawn(self, handle: RunHandle, launch: Launch) -> None:
        bus = handle.bus
        assert bus is not None
        try:
            process = subprocess.Popen(
                launch.argv,
                cwd=str(launch.cwd),
                env=launch.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            await self._fail_to_start(handle, f"failed to spawn: {exc}")
            return

        handle.process = process
        handle.status = "running"
        handle.recorder = InstrumentRecorder(self._capabilities, handle.observing, Path(handle.run_dir))
        handle.recorder.start()
        loop = asyncio.get_running_loop()
        self._loop = loop

        await bus.publish(
            "status",
            status="running",
            argv=launch.argv,
            run_dir=handle.run_dir,
            profile=handle.profile,
            target=handle.target,
            unit_serial=handle.unit_serial,
        )
        if self._on_started is not None:
            with contextlib.suppress(Exception):
                self._on_started(handle)

        run_dir = Path(handle.run_dir)
        threading.Thread(
            target=pump_stdout,
            args=(process, bus, run_dir / "test.log"),
            daemon=True,
            name=f"gauntlet-stdout-{handle.run_id}",
        ).start()
        threading.Thread(
            target=self._await_exit,
            args=(handle, process, bus, loop),
            daemon=True,
            name=f"gauntlet-wait-{handle.run_id}",
        ).start()

    def _await_exit(
        self,
        handle: RunHandle,
        process: subprocess.Popen[str],
        bus: EventBus,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Tail metrics until the process exits, then finalize."""
        tailer = threading.Thread(
            target=tail_metrics,
            args=(Path(handle.run_dir) / "metrics.jsonl", process, bus),
            daemon=True,
            name=f"gauntlet-tail-{handle.run_id}",
        )
        tailer.start()
        try:
            code = process.wait()
        except OSError:
            code = -1
        tailer.join(timeout=3.0)
        self._finalize(handle, code, bus, loop)

    def _finalize(
        self,
        handle: RunHandle,
        exit_code: int,
        bus: EventBus,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        # Stopped before anything is published, so the summary is on disk by
        # the time a caller is told the run has finished.
        if handle.recorder is not None:
            handle.recorder.stop()
        verdict = _read_verdict(Path(handle.run_dir) / "verdict.json")
        if verdict is None:
            status = "error"
            passed = False
            reason = f"suite exited with code {exit_code} without writing verdict.json"
        else:
            passed = verdict.passed
            reason = verdict.reason
            if passed:
                status = "passed"
            elif verdict.aborted:
                status = "aborted"
            else:
                status = "failed"

        handle.status = status
        handle.ended_at = _utc_iso()
        handle.duration_s = round(time.time() - _epoch(handle.started_at), 3)
        handle.verdict = {"passed": "PASS", "failed": "FAIL", "aborted": "ABORTED", "error": "ERROR"}[status]
        handle.fail_reason = reason if not passed else None

        payload = verdict.model_dump(mode="json") if verdict is not None else {}
        _schedule(
            loop,
            bus.publish("verdict", result=handle.verdict, reason=reason, summary=payload),
        )
        _schedule(
            loop,
            bus.publish("status", status=status, duration_s=handle.duration_s, exit_code=exit_code),
        )
        _schedule(loop, self._close_and_notify(handle))

    async def _close_and_notify(self, handle: RunHandle) -> None:
        if handle.release_capabilities is not None:
            handle.release_capabilities()
        if handle.bus is not None:
            await handle.bus.close()
        if self._on_completed is not None:
            with contextlib.suppress(Exception):
                await self._on_completed(handle)

    async def _fail_to_start(self, handle: RunHandle, reason: str, *, status: str = "error") -> None:
        """End a run that was created but never got as far as a process.

        Recorded like any other finished run, because the caller was handed
        this one before it could fail: the history row it wrote says
        ``starting`` until something says otherwise.
        """
        verdict = "ABORTED" if status == "aborted" else "ERROR"
        handle.status = status
        handle.ended_at = _utc_iso()
        handle.duration_s = round(time.time() - _epoch(handle.started_at), 3)
        handle.verdict = verdict
        handle.fail_reason = reason
        if handle.bus is not None:
            await handle.bus.publish("status", status=status, message=reason)
            await handle.bus.publish("verdict", result=verdict, reason=reason, summary={})
        await self._close_and_notify(handle)

    def _observed(self, required: list[str], observe: list[str]) -> list[str]:
        """Which instruments this run records, as instance keys.

        Everything the suite requires, because a run is read afterwards against
        the instruments that drove it, and then whatever else the operator
        asked for. An extra instrument is named by its instance key, the same
        key the panel shows, and one this bench does not have is refused rather
        than dropped: an operator who asked to record something is owed the
        recording or the reason there is none.
        """
        keys = [self._capabilities.resolve(name)[0] for name in required]
        for key in observe:
            provider = self._capabilities.provider(key)
            if provider is None:
                known = ", ".join(self._capabilities.instance_keys()) or "none"
                raise RunRejected(f"cannot record {key!r}: no such instrument (registered: {known})")
            if not provider.available():
                raise RunRejected(f"cannot record {key!r}: the instrument is not available")
            if key not in keys:
                keys.append(key)
        return keys

    def _resolve_profile(self, suite: Any, request: RunRequest) -> Path | None:
        if request.profile_body is not None:
            return _write_scratch_profile(self._runs_dir, suite.key, request.profile_body)
        if not request.profile:
            return None
        path = resolve_profile(suite, request.profile, self._user_profiles_dir)
        if path is None:
            raise RunRejected(f"profile {request.profile!r} not found for suite {suite.key!r}")
        return path

    def _evict_old(self) -> None:
        """Drop the oldest finished runs. History remains in SQLite."""
        finished = [h for h in self.list_runs() if h.finished]
        for handle in finished[self._history_size :]:
            self._runs.pop(handle.run_id, None)


def _read_verdict(path: Path) -> Verdict | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return Verdict.model_validate(raw)
    except Exception:
        log.warning("verdict.json at %s does not match the contract", path)
        return None


def _snapshot_profile(source: Path, run_dir: Path) -> None:
    """Copy the profile into the run directory."""
    destination = run_dir / "profile.yaml"
    if destination.exists():
        return
    with contextlib.suppress(OSError):
        destination.write_bytes(source.read_bytes())


def _write_scratch_profile(runs_dir: Path, suite_key: str, body: str) -> Path:
    """Write an inline profile body to a scratch file for one run."""
    scratch = runs_dir / "_scratch" / suite_key
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"{_new_run_id()}.yaml"
    path.write_text(body)
    return path


def _schedule(loop: asyncio.AbstractEventLoop, coro: Awaitable[Any]) -> None:
    """Run a coroutine on the loop from a reader thread."""
    try:
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(coro))
    except RuntimeError:
        # The loop is closed; there is nothing left to notify.
        coro.close()  # type: ignore[attr-defined]


def _new_run_id() -> str:
    """The UTC second the run started, then four random characters.

    The random part is what keeps two runs started in the same second apart.
    A suite that finishes in well under a second makes that ordinary, and a
    repeated id would put the second run's artifacts in the first run's
    directory and overwrite its row in the index.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(2)}"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(iso: str) -> float:
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return time.time()

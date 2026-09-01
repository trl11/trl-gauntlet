#!/usr/bin/env python3
"""Serve the rig's landing page, on the port a browser reaches without one.

    ./serve-homepage.py

A rig serves Gauntlet on 7100, which is a number someone has to be told. This
answers on 80 instead, so the bare address is enough, and points at Gauntlet
from there.

It reads no telemetry of its own. ``/api/`` is proxied to Gauntlet, whose
``/api/system/data`` already samples the host, so there is one implementation
of that and the page cannot disagree with the application about what the bench
is doing. Proxying rather than fetching 7100 from the browser also keeps the
page same-origin, which is what lets Gauntlet stay without CORS.

Nothing here is Gauntlet's, so it holds no state: the datasheets are read from
the data directory the application already owns, and survive a redeploy for
the same reason run history does.

The environment overrides every path and port:

    GAUNTLET_HOMEPAGE_PORT  what to listen on (default 80)
    GAUNTLET_URL            where Gauntlet is (default http://127.0.0.1:7100)
    GAUNTLET_DATA_DIR       where datasheets/ lives (default ~/.config/gauntlet)
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PAGE = HERE / "homepage.html"

DEFAULT_PORT = 80
DEFAULT_UPSTREAM = "http://127.0.0.1:7100"

# Long enough for a loaded rig to answer, short enough that a Gauntlet which
# has stopped answering leaves the page reporting that rather than hanging.
UPSTREAM_TIMEOUT_S = 5.0

# What a datasheet may be. Anything else in the directory is not listed and not
# served, so dropping a stray file there cannot turn this into a file server.
DATASHEET_SUFFIXES = frozenset({".csv", ".md", ".pdf", ".png", ".txt"})


def data_dir() -> Path:
    """Where Gauntlet keeps its state on this machine."""
    return Path(os.environ.get("GAUNTLET_DATA_DIR") or Path.home() / ".config" / "gauntlet")


def datasheet_dir() -> Path:
    """Where the datasheets are read from."""
    return data_dir() / "datasheets"


def datasheets() -> list[dict[str, Any]]:
    """Every datasheet that can be served, newest first.

    Each name goes back through ``resolve_datasheet`` rather than being trusted
    for having been read out of the directory, so what is listed is exactly
    what can be downloaded: a symlink pointing out of the directory is a file
    by ``is_file()`` and is refused by both.
    """
    found: list[dict[str, Any]] = []
    try:
        entries = sorted(datasheet_dir().iterdir())
    except OSError:
        return found
    for entry in entries:
        resolved = resolve_datasheet(entry.name)
        if resolved is None:
            continue
        try:
            stat = resolved.stat()
        except OSError:
            continue
        found.append({"bytes": stat.st_size, "modified": int(stat.st_mtime), "name": entry.name})
    return sorted(found, key=lambda sheet: int(sheet["modified"]), reverse=True)


def resolve_datasheet(name: str) -> Path | None:
    """The file a request names, or None if it is not a datasheet in the directory.

    The path is resolved and checked to be a direct child of the datasheet
    directory, so neither a traversal nor a symlink out of it is served.
    """
    directory = datasheet_dir().resolve()
    try:
        candidate = (directory / name).resolve()
    except OSError:
        return None
    if candidate.parent != directory:
        return None
    if candidate.suffix.lower() not in DATASHEET_SUFFIXES:
        return None
    if not candidate.is_file():
        return None
    return candidate


def upstream() -> str:
    """Where Gauntlet is, without a trailing slash."""
    return (os.environ.get("GAUNTLET_URL") or DEFAULT_UPSTREAM).rstrip("/")


class Handler(BaseHTTPRequestHandler):
    """The landing page, the datasheets, and a read-only proxy to Gauntlet."""

    server_version = "gauntlet-homepage"

    def do_GET(self) -> None:
        """Route a request, on the only method this serves."""
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/":
            self.send_page()
        elif path == "/datasheets":
            self.send_json(HTTPStatus.OK, {"datasheets": datasheets()})
        elif path.startswith("/datasheets/"):
            self.send_datasheet(path[len("/datasheets/") :])
        elif path.startswith("/api/"):
            self.send_proxied()
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"detail": "no such page"})

    def log_message(self, format: str, *args: Any) -> None:
        """Log through journald's stderr rather than to stdout.

        ``format`` and ``args`` are printf-style because that is the signature
        the base class calls this with.
        """
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        """One response, with the length browsers need to stop reading."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_datasheet(self, name: str) -> None:
        """One datasheet, if the name really is one."""
        resolved = resolve_datasheet(urllib.parse.unquote(name))
        if resolved is None:
            self.send_json(HTTPStatus.NOT_FOUND, {"detail": "no such datasheet"})
            return
        kind = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        try:
            body = resolved.read_bytes()
        except OSError as error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return
        self.send_bytes(HTTPStatus.OK, kind, body)

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        """One JSON response."""
        self.send_bytes(status, "application/json", json.dumps(payload).encode())

    def send_page(self) -> None:
        """The landing page, read per request so an edit needs no restart."""
        try:
            body = PAGE.read_bytes()
        except OSError as error:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": str(error)})
            return
        self.send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", body)

    def send_proxied(self) -> None:
        """Pass a read through to Gauntlet, and report it being down as a 502.

        The page has to render whether or not Gauntlet is up, so a refused
        connection is an answer rather than a traceback.

        The whole of ``self.path`` goes upstream rather than the routed prefix,
        so a query string reaches Gauntlet as the caller wrote it.
        """
        target = upstream() + self.path
        try:
            with urllib.request.urlopen(target, timeout=UPSTREAM_TIMEOUT_S) as response:
                body = response.read()
                kind = response.headers.get("Content-Type", "application/json")
                self.send_bytes(HTTPStatus(response.status), kind, body)
        except urllib.error.HTTPError as error:
            self.send_bytes(
                HTTPStatus(error.code),
                error.headers.get("Content-Type", "application/json"),
                error.read(),
            )
        except (OSError, ValueError) as error:
            self.send_json(HTTPStatus.BAD_GATEWAY, {"detail": f"gauntlet is not answering: {error}"})


def main() -> int:
    """Serve until stopped."""
    port = int(os.environ.get("GAUNTLET_HOMEPAGE_PORT") or DEFAULT_PORT)
    if not PAGE.is_file():
        sys.stderr.write(f"serve-homepage: {PAGE} is not beside this script\n")
        return 1

    # Created rather than required, so a rig with no datasheets yet still
    # answers the listing and someone can scp the first one in.
    try:
        datasheet_dir().mkdir(parents=True, exist_ok=True)
    except OSError as error:
        sys.stderr.write(f"serve-homepage: cannot create {datasheet_dir()}: {error}\n")

    try:
        server = ThreadingHTTPServer(("", port), Handler)
    except PermissionError:
        sys.stderr.write(
            f"serve-homepage: not allowed to bind port {port}.\n"
            "  A port below 1024 needs either root or a host that allows it:\n"
            "  sudo ./setup-host.sh installs the sysctl that does.\n"
        )
        return 1
    except OSError as error:
        sys.stderr.write(f"serve-homepage: cannot listen on port {port}: {error}\n")
        return 1

    sys.stderr.write(f"serve-homepage: serving on port {port}, proxying {upstream()}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

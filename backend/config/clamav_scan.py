"""Bounded ClamD INSTREAM client for the private attachment scanner command.

This process deliberately has no Django or third-party dependencies. Run it as
``python /app/config/clamav_scan.py FILE`` or use ``--ping`` for readiness.
The daemon must be on a trusted private network: ClamD TCP has no TLS/auth.
Exit codes match ClamAV: 0 clean/ready, 1 infected, 2 operational failure.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import math
import os
from pathlib import Path
import queue
import re
import socket
import stat
import struct
import sys
import threading
import time
from typing import BinaryIO, Iterator, Mapping


CHUNK_BYTES = 64 * 1024
MAX_REPLY_BYTES = 4096
HARD_MAX_FILE_BYTES = 10 * 1024 * 1024


class ScannerError(ValueError):
    """A bounded, safe-to-display operational failure; never a clean result."""


@dataclass(frozen=True)
class Configuration:
    host: str = "clamav"
    port: int = 3310
    timeout: float = 45.0
    max_file_bytes: int = HARD_MAX_FILE_BYTES
    max_database_age_days: int = 7

    def __post_init__(self) -> None:
        try:
            ipaddress.ip_address(self.host)
            valid_host = True
        except ValueError:
            valid_host = bool(re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?",
                self.host,
            ))
        if not valid_host or len(self.host) > 253:
            raise ScannerError("Invalid CLAMAV_HOST.")
        if not 1 <= self.port <= 65535:
            raise ScannerError("CLAMAV_PORT must be between 1 and 65535.")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 300:
            raise ScannerError("CLAMAV_TIMEOUT must be finite and between 0 and 300 seconds.")
        if not 1 <= self.max_file_bytes <= HARD_MAX_FILE_BYTES:
            raise ScannerError("CLAMAV_MAX_FILE_BYTES must be between 1 and 10485760.")
        if not 1 <= self.max_database_age_days <= 14:
            raise ScannerError("CLAMAV_MAX_DATABASE_AGE_DAYS must be between 1 and 14.")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> Configuration:
        environment = os.environ if environment is None else environment
        try:
            return cls(
                host=environment.get("CLAMAV_HOST", "clamav").strip(),
                port=int(environment.get("CLAMAV_PORT", "3310")),
                timeout=float(environment.get("CLAMAV_TIMEOUT", "45")),
                max_file_bytes=int(environment.get("CLAMAV_MAX_FILE_BYTES", str(HARD_MAX_FILE_BYTES))),
                max_database_age_days=int(environment.get("CLAMAV_MAX_DATABASE_AGE_DAYS", "7")),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ScannerError):
                raise
            raise ScannerError("Invalid ClamAV connection or size configuration.") from error


def remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ScannerError("ClamAV scan deadline exceeded.")
    return seconds


def resolve_addresses(configuration: Configuration, deadline: float) -> list:
    # getaddrinfo itself has no timeout. A daemon resolver thread lets the CLI
    # exit on its deadline even when the operating-system DNS resolver stalls.
    results: queue.Queue = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            results.put(socket.getaddrinfo(configuration.host, configuration.port, type=socket.SOCK_STREAM))
        except OSError as error:
            results.put(error)

    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses = results.get(timeout=remaining(deadline))
    except queue.Empty as error:
        raise ScannerError("ClamAV name resolution exceeded its deadline.") from error
    if isinstance(addresses, OSError) or not addresses:
        raise ScannerError("ClamAV name resolution failed.")
    return addresses


@contextmanager
def connect(configuration: Configuration, deadline: float) -> Iterator[socket.socket]:
    connection = None
    for family, socket_type, protocol, _, address in resolve_addresses(configuration, deadline):
        candidate = socket.socket(family, socket_type, protocol)
        try:
            candidate.settimeout(remaining(deadline))
            candidate.connect(address)
        except (OSError, ScannerError):
            candidate.close()
            remaining(deadline)
        else:
            connection = candidate
            break
    if connection is None:
        raise ScannerError("ClamAV daemon is unavailable.")
    try:
        yield connection
    finally:
        connection.close()


def send(connection: socket.socket, payload: bytes, deadline: float) -> None:
    connection.settimeout(remaining(deadline))
    connection.sendall(payload)


def receive_reply(connection: socket.socket, deadline: float) -> str:
    reply = bytearray()
    while True:
        connection.settimeout(remaining(deadline))
        part = connection.recv(min(1024, MAX_REPLY_BYTES + 1 - len(reply)))
        if not part:
            break
        reply.extend(part)
        if len(reply) > MAX_REPLY_BYTES:
            raise ScannerError("ClamAV reply exceeded its size limit.")
    if not reply.endswith(b"\0") or reply.count(b"\0") != 1:
        raise ScannerError("ClamAV returned an incomplete or ambiguous reply.")
    try:
        value = reply[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ScannerError("ClamAV returned an invalid reply encoding.") from error
    if not value or any(not character.isprintable() for character in value):
        raise ScannerError("ClamAV returned an invalid reply record.")
    return value


def file_identity(metadata: os.stat_result, *, include_change_time: bool = False) -> tuple:
    identity = (
        metadata.st_dev, metadata.st_ino, metadata.st_size,
        metadata.st_mtime_ns,
    )
    # Windows lstat/fstat may expose different historical meanings for ctime.
    # Compare ctime only between descriptor snapshots from the same API.
    return (*identity, metadata.st_ctime_ns) if include_change_time else identity


def regular_file(metadata: os.stat_result) -> bool:
    return stat.S_ISREG(metadata.st_mode) and not (
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


@contextmanager
def open_regular_file(path: Path, max_file_bytes: int) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    before = path.lstat()
    if not regular_file(before) or before.st_nlink != 1:
        raise ScannerError("Only a single-link regular attachment file can be scanned.")
    if not 0 < before.st_size <= max_file_bytes:
        raise ScannerError("Attachment is empty or exceeds the scanner size limit.")
    # NONBLOCK prevents a path swapped to a FIFO between lstat/open from hanging.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not regular_file(opened) or opened.st_nlink != 1 or file_identity(opened) != file_identity(before):
            raise ScannerError("Attachment changed before scanning.")
        with os.fdopen(descriptor, "rb") as sample:
            descriptor = -1
            yield sample, opened
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def scan_file(path: str | os.PathLike, configuration: Configuration | None = None) -> tuple[int, str]:
    configuration = Configuration.from_environment() if configuration is None else configuration
    deadline = time.monotonic() + configuration.timeout
    path = Path(path)
    with open_regular_file(path, configuration.max_file_bytes) as (sample, original):
        daemon_version(configuration, deadline)
        with connect(configuration, deadline) as connection:
            send(connection, b"zINSTREAM\0", deadline)
            sent = 0
            while True:
                remaining(deadline)
                chunk = sample.read(CHUNK_BYTES)
                if not chunk:
                    break
                sent += len(chunk)
                if sent > configuration.max_file_bytes:
                    raise ScannerError("Attachment grew beyond the scanner size limit.")
                send(connection, struct.pack("!I", len(chunk)), deadline)
                send(connection, chunk, deadline)
            if sent != original.st_size or file_identity(os.fstat(sample.fileno()), include_change_time=True) != file_identity(original, include_change_time=True):
                raise ScannerError("Attachment changed during scanning.")
            send(connection, b"\0\0\0\0", deadline)
            reply = receive_reply(connection, deadline)
        after = path.lstat()
        if not regular_file(after) or after.st_nlink != 1 or file_identity(after) != file_identity(original):
            raise ScannerError("Attachment changed before the scan result was received.")
    if reply == "stream: OK":
        return 0, "ClamAV scan completed successfully."
    if reply.startswith("stream: ") and reply.endswith(" FOUND"):
        signature = reply[len("stream: "):-len(" FOUND")]
        if signature and len(signature) <= 512:
            return 1, "ClamAV reported malware: " + signature
    raise ScannerError("ClamAV did not return an explicit clean or infected result.")


def daemon_version(configuration: Configuration, deadline: float) -> str:
    """A daemon left running with stale signatures must never report clean."""
    with connect(configuration, deadline) as connection:
        send(connection, b"zVERSIONCOMMANDS\0", deadline)
        reply = receive_reply(connection, deadline)
    version, separator, capabilities = reply.partition("| COMMANDS:")
    match = re.fullmatch(r"ClamAV [^/]+/[1-9][0-9]*/(.+)", version.strip())
    if not separator or not match or "INSTREAM" not in capabilities.split():
        raise ScannerError("ClamAV has no loaded signature database or INSTREAM capability.")
    date = re.fullmatch(
        r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) +"
        r"([0-9]{1,2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})",
        match.group(1),
    )
    if not date:
        raise ScannerError("ClamAV signature database date could not be verified.")
    weekday, month, day, hour, minute, second, year = date.groups()
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    try:
        # The scanner container must set TZ=UTC; the protocol omits timezone.
        timestamp = datetime(int(year), months.index(month) + 1, int(day), int(hour), int(minute), int(second), tzinfo=timezone.utc)
    except ValueError as error:
        raise ScannerError("ClamAV signature database date is invalid.") from error
    if weekdays[timestamp.weekday()] != weekday:
        raise ScannerError("ClamAV signature database weekday is invalid.")
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if age < -300 or age > configuration.max_database_age_days * 86400:
        raise ScannerError("ClamAV signature database is stale or its date is in the future.")
    remaining(deadline)
    return version.strip()


def ping(configuration: Configuration | None = None) -> str:
    configuration = Configuration.from_environment() if configuration is None else configuration
    deadline = time.monotonic() + configuration.timeout
    with connect(configuration, deadline) as connection:
        send(connection, b"zPING\0", deadline)
        if receive_reply(connection, deadline) != "PONG":
            raise ScannerError("ClamAV did not acknowledge readiness.")
    version = daemon_version(configuration, deadline)
    return "ClamAV ready: " + version


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="One private regular attachment file")
    parser.add_argument("--ping", action="store_true", help="Check daemon, loaded signature database and streaming support")
    arguments = parser.parse_args(arguments)
    if arguments.ping == bool(arguments.path):
        parser.error("provide exactly one file path or --ping")
    try:
        if arguments.ping:
            print(ping())
            return 0
        code, detail = scan_file(arguments.path)
        print(detail)
        return code
    except ScannerError as error:
        print("ClamAV scan unavailable: " + str(error), file=sys.stderr)
    except (OSError, TimeoutError) as error:
        print("ClamAV scan unavailable: " + type(error).__name__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

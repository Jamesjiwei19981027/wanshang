"""Exercise real TCP framing and hostile/error cases without a malware engine."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from config import clamav_scan as scanner


def recv_exact(connection, length):
    result = bytearray()
    while len(result) < length:
        part = connection.recv(length - len(result))
        if not part:
            raise EOFError("Client disconnected")
        result.extend(part)
    return bytes(result)


def version_reply(timestamp=None):
    timestamp = datetime.now(timezone.utc) if timestamp is None else timestamp
    stamp = timestamp.strftime("%a %b %d %H:%M:%S %Y")
    return f"ClamAV 1.5.1/28402/{stamp}| COMMANDS: PING VERSIONCOMMANDS INSTREAM\0".encode()


@contextmanager
def fake_clamd(replies, *, delay=0, after_stream=None, scan_readiness=True):
    """A local daemon accepting one connection per supplied response."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)
    stopped = threading.Event()
    received = []
    failures = []

    def daemon():
        responses = [[version_reply()], *replies] if scan_readiness else replies
        for response_index, reply in enumerate(responses):
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                    break
                except socket.timeout:
                    continue
                except OSError:
                    return
            else:
                return
            try:
                with connection:
                    connection.settimeout(2)
                    command = bytearray()
                    while not command.endswith(b"\0"):
                        command.extend(recv_exact(connection, 1))
                        if len(command) > 100:
                            raise AssertionError("Unbounded client command")
                    record = {"command": bytes(command), "chunks": [], "data": b""}
                    if command == b"zINSTREAM\0":
                        content = bytearray()
                        while True:
                            length = struct.unpack("!I", recv_exact(connection, 4))[0]
                            if not length:
                                record["terminated"] = True
                                break
                            if length > scanner.CHUNK_BYTES:
                                raise AssertionError("Unexpected unbounded frame")
                            record["chunks"].append(length)
                            content.extend(recv_exact(connection, length))
                        record["data"] = bytes(content)
                        if after_stream:
                            after_stream()
                    if not scan_readiness or response_index:
                        received.append(record)
                    for part in reply:
                        if delay:
                            time.sleep(delay)
                        connection.sendall(part)
            except (OSError, EOFError):
                # Clients intentionally close on timeout and malformed replies.
                pass
            except Exception as error:
                failures.append(error)

    thread = threading.Thread(target=daemon, daemon=True)
    thread.start()
    configuration = scanner.Configuration(host="127.0.0.1", port=listener.getsockname()[1], timeout=2)
    try:
        yield configuration, received
    finally:
        stopped.set()
        listener.close()
        thread.join(timeout=2)
        if failures:
            raise failures[0]


class ClamAVStreamingClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="axle-clamd-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private drawing ; $(literal).pdf"
        self.path.write_bytes(b"%PDF-1.7\nA harmless test document.\n")

    def test_stream_frames_all_bytes_and_handles_fragmented_reply(self):
        content = bytes(range(256)) * (scanner.CHUNK_BYTES // 256) + b"x" * 123
        self.path.write_bytes(content)
        with fake_clamd([[b"st", b"ream: ", b"O", b"K\0"]], delay=0.005) as (configuration, received):
            code, _ = scanner.scan_file(self.path, configuration)
        self.assertEqual(code, 0)
        self.assertEqual(received[0]["command"], b"zINSTREAM\0")
        self.assertEqual(received[0]["chunks"], [scanner.CHUNK_BYTES, 123])
        self.assertEqual(received[0]["data"], content)
        self.assertTrue(received[0]["terminated"])
        self.assertNotIn(str(self.path).encode(), received[0]["command"])

    def test_infection_has_distinct_exit_code_and_never_deletes_file(self):
        original = self.path.read_bytes()
        with fake_clamd([[b"stream: Eicar-Signature FOUND\0"]]) as (configuration, _):
            code, detail = scanner.scan_file(self.path, configuration)
        self.assertEqual(code, 1)
        self.assertIn("Eicar-Signature", detail)
        self.assertEqual(self.path.read_bytes(), original)

    def test_only_an_explicit_single_clean_record_can_authorize_download(self):
        for response in (
            b"", b"stream: OK", b"OK\0", b"stream: OK\0stream: Virus FOUND\0",
            b"stream: OK\0unexpected", b"stream: OK\n\0", b"stream: \xff FOUND\0",
            b"stream: Access denied ERROR\0", b"INSTREAM size limit exceeded. ERROR\0",
            b"stream:  FOUND\0", b"stream: " + b"a" * 513 + b" FOUND\0",
        ):
            with self.subTest(response=response[:80]):
                with fake_clamd([[response]]) as (configuration, _):
                    with self.assertRaises(scanner.ScannerError):
                        scanner.scan_file(self.path, configuration)

    def test_oversized_daemon_reply_is_rejected(self):
        with fake_clamd([[b"a" * (scanner.MAX_REPLY_BYTES + 1)]]) as (configuration, _):
            with self.assertRaisesRegex(scanner.ScannerError, "reply exceeded"):
                scanner.scan_file(self.path, configuration)

    def test_slow_reply_cannot_extend_the_whole_operation_deadline(self):
        with fake_clamd([[bytes([value]) for value in b"stream: OK\0"]], delay=0.06) as (configuration, _):
            configuration = scanner.Configuration(configuration.host, configuration.port, timeout=0.13)
            start = time.monotonic()
            with self.assertRaises((scanner.ScannerError, TimeoutError)):
                scanner.scan_file(self.path, configuration)
            self.assertLess(time.monotonic() - start, 0.7)

    def test_stalled_dns_is_bounded_before_connecting(self):
        release = threading.Event()

        def stalled_resolver(*args, **kwargs):
            release.wait(timeout=2)
            return []

        try:
            with patch.object(scanner.socket, "getaddrinfo", side_effect=stalled_resolver):
                start = time.monotonic()
                with self.assertRaisesRegex(scanner.ScannerError, "resolution exceeded"):
                    scanner.scan_file(self.path, scanner.Configuration(timeout=0.04))
                self.assertLess(time.monotonic() - start, 0.5)
        finally:
            release.set()

    def test_changed_file_cannot_inherit_a_clean_result(self):
        with fake_clamd([[b"stream: OK\0"]], after_stream=lambda: self.path.write_bytes(b"changed")) as (configuration, _):
            with self.assertRaisesRegex(scanner.ScannerError, "changed"):
                scanner.scan_file(self.path, configuration)

    def test_unacceptable_files_are_rejected_before_network_io(self):
        cases = [(Path(self.directory.name), "directory")]
        empty = Path(self.directory.name) / "empty"
        empty.touch()
        cases.append((empty, "empty"))
        large = Path(self.directory.name) / "large"
        large.write_bytes(b"x" * 17)
        cases.append((large, "oversized"))
        for path, reason in cases:
            with self.subTest(reason=reason), patch.object(scanner, "connect") as connection:
                with self.assertRaises(scanner.ScannerError):
                    scanner.scan_file(path, scanner.Configuration(max_file_bytes=16))
                connection.assert_not_called()

    def test_file_swap_to_nonregular_object_before_open_is_rejected(self):
        before = self.path.lstat()
        with patch.object(scanner.os, "fstat", return_value=Path(self.directory.name).stat()):
            with self.assertRaises(scanner.ScannerError):
                with scanner.open_regular_file(self.path, scanner.HARD_MAX_FILE_BYTES):
                    self.fail("Directory descriptor was accepted")
        self.assertEqual(self.path.stat().st_size, before.st_size)

    def test_symlink_is_rejected_without_following_it(self):
        link = Path(self.directory.name) / "linked.pdf"
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("This Windows account cannot create symlinks; Linux CI executes this test")
        with patch.object(scanner, "connect") as connection:
            with self.assertRaises(scanner.ScannerError):
                scanner.scan_file(link)
            connection.assert_not_called()

    def test_hard_link_is_rejected(self):
        link = Path(self.directory.name) / "hard-linked.pdf"
        try:
            os.link(self.path, link)
        except OSError:
            self.skipTest("Hard links are not available on this filesystem")
        with self.assertRaises(scanner.ScannerError):
            scanner.scan_file(link)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is a Linux-only case")
    def test_fifo_does_not_block_file_open(self):
        fifo = Path(self.directory.name) / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(scanner.ScannerError):
            scanner.scan_file(fifo)


class ClamAVReadinessAndConfigurationTests(unittest.TestCase):
    def test_readiness_checks_loaded_database_and_instream_capability(self):
        with fake_clamd([
            [b"PONG\0"],
            [version_reply()],
        ], scan_readiness=False) as (configuration, received):
            self.assertIn("28402", scanner.ping(configuration))
        self.assertEqual([item["command"] for item in received], [b"zPING\0", b"zVERSIONCOMMANDS\0"])

    def test_open_port_or_ping_without_database_is_not_healthy(self):
        for version in (
            b"ClamAV 1.5.1\0",
            b"ClamAV 1.5.1/0/date| COMMANDS: INSTREAM\0",
            b"ClamAV 1.5.1/28402/date| COMMANDS: PING\0",
            b"COMMAND UNAVAILABLE\0",
        ):
            with self.subTest(version=version):
                with fake_clamd([[b"PONG\0"], [version]], scan_readiness=False) as (configuration, _):
                    with self.assertRaises(scanner.ScannerError):
                        scanner.ping(configuration)

    def test_wrong_pong_is_not_ready(self):
        with fake_clamd([[b"ERROR\0"]], scan_readiness=False) as (configuration, _):
            with self.assertRaises(scanner.ScannerError):
                scanner.ping(configuration)

    def test_configuration_rejects_urls_controls_and_invalid_bounds(self):
        for overrides in (
            {"host": "https://clamav/"}, {"host": "clamav\nINSTREAM"}, {"host": "clamav:3310"},
            {"host": ""}, {"host": "clamav\x1b"}, {"port": 0}, {"port": 65536},
            {"timeout": 0}, {"timeout": float("nan")}, {"timeout": float("inf")},
            {"max_file_bytes": 0}, {"max_file_bytes": scanner.HARD_MAX_FILE_BYTES + 1},
            {"max_database_age_days": 0}, {"max_database_age_days": 15},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(scanner.ScannerError):
                    scanner.Configuration(**overrides)
        self.assertEqual(scanner.Configuration.from_environment({}).host, "clamav")
        self.assertEqual(scanner.Configuration(host="::1").host, "::1")

    def test_every_scan_checks_database_age_before_sending_attachment_bytes(self):
        now = datetime.now(timezone.utc)
        responses = (
            version_reply(now - timedelta(days=8)),
            version_reply(now + timedelta(hours=1)),
            b"ClamAV 1.5.1/28402/unparseable-date| COMMANDS: INSTREAM\0",
        )
        with tempfile.TemporaryDirectory(prefix="axle-stale-db-") as directory:
            path = Path(directory) / "private.pdf"
            path.write_bytes(b"%PDF-1.7 harmless")
            for response in responses:
                with self.subTest(response=response):
                    with fake_clamd([[response]], scan_readiness=False) as (configuration, received):
                        with self.assertRaises(scanner.ScannerError):
                            scanner.scan_file(path, configuration)
                    self.assertEqual([item["command"] for item in received], [b"zVERSIONCOMMANDS\0"])

    def test_long_running_daemon_with_old_database_is_not_healthy(self):
        with fake_clamd([
            [b"PONG\0"],
            [version_reply(datetime.now(timezone.utc) - timedelta(days=8))],
        ], scan_readiness=False) as (configuration, _):
            with self.assertRaisesRegex(scanner.ScannerError, "stale"):
                scanner.ping(configuration)

    def test_cli_maps_unavailable_daemon_to_error_instead_of_clean(self):
        with patch.object(scanner, "scan_file", side_effect=OSError("sensitive path")):
            with patch("sys.stderr", new_callable=io.StringIO) as output:
                self.assertEqual(scanner.main(["a-file"]), 2)
                self.assertIn("OSError", output.getvalue())
                self.assertNotIn("sensitive path", output.getvalue())

    def test_cli_preserves_clean_and_infected_exit_codes(self):
        for code in (0, 1):
            with patch.object(scanner, "scan_file", return_value=(code, "bounded result")):
                with patch("sys.stdout", new_callable=io.StringIO):
                    self.assertEqual(scanner.main(["a-file"]), code)

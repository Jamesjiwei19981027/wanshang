"""Security and recovery tests; Docker operations in shell-flow tests are fakes."""
from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
import backup_manifest
import media_archive


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="axle-media-test-")
        self.base = Path(self.temporary.name)
        self.roots = {name: self.base / name for name in media_archive.ROOT_NAMES}
        for root in self.roots.values():
            root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self):
        output = io.BytesIO()
        media_archive.dump(output, self.roots)
        return output.getvalue()

    def crafted(self, members):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, kind, payload in members:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind == tarfile.REGTYPE:
                    member.size = len(payload)
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = "../../outside"
                archive.addfile(member, io.BytesIO(payload) if member.isfile() else None)
        return output.getvalue()

    def empty_target(self):
        target = {name: self.base / ("restore-" + name) for name in media_archive.ROOT_NAMES}
        for root in target.values():
            root.mkdir()
        return target

    def test_binary_roundtrip_and_private_permissions(self):
        (self.roots["public_media"] / "catalogue").mkdir()
        public = b"public document\x00\xff"
        private = b"private drawing\x00\x01\xff"
        (self.roots["public_media"] / "catalogue" / "spec.pdf").write_bytes(public)
        (self.roots["private_media"] / "drawing.png").write_bytes(private)
        archive = self.archive()
        archive_path = self.base / "media.tar.gz"
        archive_path.write_bytes(archive)
        self.assertEqual(media_archive.inspect_archive(archive_path)["file_count"], 2)
        target = self.empty_target()
        self.assertEqual(media_archive.restore(io.BytesIO(archive), target)["restored_file_count"], 2)
        self.assertEqual((target["public_media"] / "catalogue/spec.pdf").read_bytes(), public)
        self.assertEqual((target["private_media"] / "drawing.png").read_bytes(), private)
        if os.name == "posix":
            self.assertEqual((target["private_media"] / "drawing.png").stat().st_mode & 0o777, 0o600)
            self.assertEqual(target["private_media"].stat().st_mode & 0o777, 0o700)

    def test_refuse_existing_restore_files_without_modification(self):
        archive = self.archive()
        target = self.empty_target()
        kept = target["private_media"] / "keep"
        kept.write_text("keep")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            media_archive.restore(io.BytesIO(archive), target)
        self.assertEqual(kept.read_text(), "keep")

    def test_backup_rejects_symlink_hardlink_and_fifo(self):
        if os.name != "posix":
            self.skipTest("POSIX file types")
        outside = self.base / "outside"
        outside.write_bytes(b"do not archive")
        link = self.roots["private_media"] / "link"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.archive()
        link.unlink()
        os.link(outside, link)
        with self.assertRaises(ValueError):
            self.archive()
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(ValueError):
            self.archive()

    def test_restore_rejects_traversal_links_devices_and_duplicate_paths(self):
        roots = [("public_media", tarfile.DIRTYPE, b""), ("private_media", tarfile.DIRTYPE, b"")]
        bad = [
            [("../outside", tarfile.REGTYPE, b"bad")],
            [("/private_media/outside", tarfile.REGTYPE, b"bad")],
            [("private_media/../outside", tarfile.REGTYPE, b"bad")],
            [("private_media\\outside", tarfile.REGTYPE, b"bad")],
            [("private_media/link", tarfile.SYMTYPE, b"")],
            [("private_media/link", tarfile.LNKTYPE, b"")],
            [("private_media/device", tarfile.CHRTYPE, b"")],
            [("private_media/item", tarfile.REGTYPE, b"one"), ("private_media/item", tarfile.REGTYPE, b"two")],
        ]
        for index, entries in enumerate(bad):
            with self.subTest(index=index):
                archive_path = self.base / f"bad-{index}.tar.gz"
                archive_path.write_bytes(self.crafted(roots + entries))
                with self.assertRaises((ValueError, tarfile.TarError)):
                    media_archive.inspect_archive(archive_path)
        self.assertFalse((self.base / "outside").exists())

    def test_missing_roots_and_oversized_entries_rejected(self):
        archive = self.base / "missing.tar.gz"
        archive.write_bytes(self.crafted([("public_media", tarfile.DIRTYPE, b"")]))
        with self.assertRaisesRegex(ValueError, "missing"):
            media_archive.inspect_archive(archive)
        member = tarfile.TarInfo("private_media/oversized")
        member.size = media_archive.MAX_FILE_BYTES + 1
        with self.assertRaisesRegex(ValueError, "oversized"):
            media_archive.safe_member(member, {"private_media": True})


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="axle-manifest-test-")
        self.base = Path(self.temporary.name)
        self.backup = self.base / "backup"
        self.backup.mkdir()
        roots = {name: self.base / name for name in media_archive.ROOT_NAMES}
        for root in roots.values():
            root.mkdir()
        (roots["private_media"] / "sample.bin").write_bytes(b"owned test bytes")
        (self.backup / "database.dump").write_bytes(b"PGDMP-test-fixture-not-a-real-database")
        with (self.backup / "media.tar.gz").open("wb") as output:
            media_archive.dump(output, roots)

    def tearDown(self):
        self.temporary.cleanup()

    def test_manifest_integrity_and_tamper_rejection(self):
        backup_manifest.create(self.backup, "axle-test", self.base)
        self.assertTrue(backup_manifest.verify(self.backup)["backup_verified"])
        with (self.backup / "database.dump").open("ab") as output:
            output.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            backup_manifest.verify(self.backup)

    def test_linked_dump_rejected(self):
        if os.name != "posix":
            self.skipTest("POSIX links")
        os.link(self.backup / "database.dump", self.base / "alias")
        with self.assertRaisesRegex(ValueError, "links"):
            backup_manifest.create(self.backup, "axle-test", self.base)

    def test_backup_root_refuses_unrelated_directory(self):
        unrelated = self.base / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep").write_text("keep")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            backup_manifest.prepare_root(unrelated)
        self.assertEqual((unrelated / "keep").read_text(), "keep")
        dedicated = self.base / "dedicated"
        backup_manifest.prepare_root(dedicated)
        backup_manifest.prepare_root(dedicated)
        self.assertEqual((dedicated / backup_manifest.MARKER).read_text(), "axle-production-backups-v1\n")
        if os.name == "posix":
            self.assertEqual(dedicated.stat().st_mode & 0o777, 0o700)

    def test_invalid_format_and_truncated_archive_fail(self):
        (self.backup / "database.dump").write_bytes(b"not PostgreSQL")
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            backup_manifest.create(self.backup, "axle-test", self.base)
        (self.backup / "database.dump").write_bytes(b"PGDMP-test")
        raw = (self.backup / "media.tar.gz").read_bytes()
        (self.backup / "media.tar.gz").write_bytes(raw[:-8])
        with self.assertRaises((EOFError, OSError, tarfile.TarError)):
            backup_manifest.create(self.backup, "axle-test", self.base)


class ShellFlowTests(unittest.TestCase):
    """Run real Bash/control flow; replace only Docker, never start a server."""

    def setUp(self):
        if os.name != "posix" or not shutil.which("bash") or not shutil.which("flock"):
            self.skipTest("Linux Bash and flock are required")
        self.temporary = tempfile.TemporaryDirectory(prefix="axle-shell-test-")
        self.base = Path(self.temporary.name)
        deploy = self.base / "deploy"
        shutil.copytree(BIN, deploy / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        self.deploy = deploy
        (deploy / "compose.production.yml").write_text("services: {}\n")
        (deploy / "configure.py").write_text("print('Preflight fixture passed')\n")
        self.env_file = deploy / ".env.production"
        self.env_file.write_text("DEPLOY_PROJECT_NAME=axle-fixture\nFIXTURE_ONLY=yes\n")
        self.env_file.chmod(0o600)
        self.mock_bin = self.base / "mock-bin"
        self.mock_bin.mkdir()
        driver = self.mock_bin / "docker"
        driver.write_text((Path(__file__).parent / "mock_docker.py").read_text())
        driver.chmod(0o700)
        self.state = self.base / "state.json"
        self.log = self.base / "docker-log.jsonl"
        self.public = self.base / "public"
        self.private = self.base / "private"
        self.public.mkdir()
        self.private.mkdir()
        (self.public / "catalogue.txt").write_bytes(b"fixture catalogue")
        (self.private / "drawing.bin").write_bytes(b"private fixture")
        self.set_state(existing=True, running=["postgres", "redis", "api", "celery"])
        self.environment = os.environ.copy()
        self.environment.update({"PATH": str(self.mock_bin) + os.pathsep + self.environment["PATH"],
                                 "AXLE_TEST_STATE": str(self.state), "AXLE_TEST_LOG": str(self.log),
                                 "AXLE_TEST_BIN": str(deploy / "bin")})
        self.environment.pop("AXLE_LOCK_PROJECT", None)

    def tearDown(self):
        if hasattr(self, "temporary"):
            self.temporary.cleanup()

    def set_state(self, *, existing, running, **extra):
        state = {"existing": existing, "running": running, "public": str(self.public), "private": str(self.private), "db_tables": 0}
        state.update(extra)
        self.state.write_text(json.dumps(state))

    def run_script(self, name, *arguments, flags=None):
        environment = self.environment.copy()
        environment.update(flags or {})
        return subprocess.run(["bash", str(self.deploy / "bin" / name), "--project", "axle-fixture", "--env-file", str(self.env_file), *map(str, arguments)],
                              text=True, capture_output=True, timeout=25, env=environment)

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def make_backup(self):
        result = self.run_script("backup.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(result.stdout.strip())

    def test_backup_resumes_exact_previous_services_and_writes_manifest(self):
        path = self.make_backup()
        self.assertTrue(backup_manifest.verify(path)["backup_verified"])
        self.assertEqual(set(json.loads(self.state.read_text())["running"]), {"postgres", "redis", "api", "celery"})
        stops = [call for call in self.commands() if call.get("command") == "stop"]
        self.assertEqual(stops[0]["services"], ["api", "celery"])
        self.assertFalse(any("--fake" in json.dumps(call) or "seed_demo" in json.dumps(call) for call in self.commands()))

    def test_backup_failure_resumes_services_and_never_marks_complete(self):
        result = self.run_script("backup.sh", flags={"AXLE_TEST_FAIL_DUMP": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(set(json.loads(self.state.read_text())["running"]), {"postgres", "redis", "api", "celery"})
        self.assertFalse(list((self.deploy / "backups").glob("backup-*")))
        self.assertTrue(list((self.deploy / "backups").glob(".incomplete-*")))

    def test_start_backs_up_before_migrate_and_keeps_apps_stopped_during_migrate(self):
        result = self.run_script("start.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.commands()
        dump_index = next(i for i, call in enumerate(commands) if call.get("operation") == "pg_dump")
        migrate_index = next(i for i, call in enumerate(commands) if call.get("operation") == "migrate")
        guard_index = next(i for i, call in enumerate(commands) if call.get("operation") == "migration_guard")
        infrastructure_index = next(i for i, call in enumerate(commands) if call.get("command") == "up" and "redis" in call.get("services", []))
        self.assertLess(dump_index, migrate_index)
        self.assertLess(guard_index, infrastructure_index)
        self.assertEqual(next(call for call in commands if call.get("command") == "build")["services"], ["api", "frontend", "nginx"])
        self.assertEqual(commands[migrate_index]["writers_running"], [])
        self.assertTrue(json.loads(self.state.read_text())["migrated"])

    def test_failed_migration_leaves_writers_stopped_without_auto_restore(self):
        result = self.run_script("update.sh", flags={"AXLE_TEST_FAIL_MIGRATE": "1"})
        self.assertNotEqual(result.returncode, 0)
        state = json.loads(self.state.read_text())
        self.assertFalse(set(state["running"]) & {"nginx", "frontend", "api", "celery"})
        self.assertTrue(list((self.deploy / "backups").glob("backup-*")))
        self.assertFalse(any(call.get("operation") == "pg_restore" for call in self.commands()))
        records = list((self.deploy / "releases" / "axle-fixture").glob("*.json"))
        self.assertEqual(json.loads(records[0].read_text())["status"], "failed-after-migrate")

    def test_divergent_history_refuses_before_infrastructure_update_and_resumes_old_services(self):
        result = self.run_script("update.sh", flags={"AXLE_TEST_FAIL_GUARD": "1"})
        self.assertNotEqual(result.returncode, 0)
        state = json.loads(self.state.read_text())
        self.assertEqual(set(state["running"]), {"postgres", "redis", "api", "celery"})
        self.assertFalse(any(call.get("command") == "up" and "redis" in call.get("services", []) for call in self.commands()))

    def test_failed_infrastructure_update_leaves_writers_stopped(self):
        result = self.run_script("update.sh", flags={"AXLE_TEST_FAIL_INFRASTRUCTURE": "1"})
        self.assertNotEqual(result.returncode, 0)
        state = json.loads(self.state.read_text())
        self.assertFalse(set(state["running"]) & {"nginx", "frontend", "api", "celery"})
        self.assertFalse(any(call.get("operation") == "migrate" for call in self.commands()))
        records = list((self.deploy / "releases" / "axle-fixture").glob("*.json"))
        self.assertEqual(json.loads(records[0].read_text())["status"], "failed-after-infrastructure")

    def test_project_override_must_match_environment(self):
        result = subprocess.run(["bash", str(self.deploy / "bin/status.sh"), "--project", "axle-unrelated", "--env-file", str(self.env_file)],
                                text=True, capture_output=True, timeout=10, env=self.environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must match", result.stderr)

    def test_active_project_lock_refuses_second_mutation(self):
        import fcntl
        lock_dir = self.deploy / ".locks"
        lock_dir.mkdir()
        with (lock_dir / "axle-fixture.lock").open("w") as locked:
            fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_script("backup.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Another deployment/backup operation", result.stderr)
        self.assertFalse(any(call.get("command") in ("up", "stop", "start", "run", "exec") for call in self.commands()))

    def test_role_bootstrap_refuses_reserved_and_shared_credentials(self):
        hook = Path(__file__).resolve().parents[1] / "postgres/init-app-role.sh"
        environment = self.environment.copy()
        environment.update({"POSTGRES_DB": "fixture", "POSTGRES_USER": "axle_admin", "POSTGRES_PASSWORD": "admin-password-" + "x" * 32,
                            "APP_DB_USER": "pg_reserved", "APP_DB_PASSWORD": "app-password-" + "y" * 32})
        rejected = subprocess.run(["sh", str(hook)], env=environment, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("reserved", rejected.stderr)
        environment["APP_DB_USER"] = "axle_app"
        environment["APP_DB_PASSWORD"] = environment["POSTGRES_PASSWORD"]
        rejected = subprocess.run(["sh", str(hook)], env=environment, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("passwords must be different", rejected.stderr)

    def test_restore_refuses_existing_target_before_any_mutation(self):
        backup = self.make_backup()
        before = self.state.read_bytes()
        self.log.write_text("")
        result = self.run_script("restore.sh", backup)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(any(call.get("command") in ("up", "stop", "start", "run", "exec") for call in self.commands()))

    def test_restore_new_project_restores_bytes_but_never_starts_application(self):
        backup = self.make_backup()
        for root in (self.public, self.private):
            for child in root.iterdir():
                child.unlink()
        self.set_state(existing=False, running=[])
        self.log.write_text("")
        result = self.run_script("restore.sh", backup)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state.read_text())
        self.assertTrue(state["restored"])
        self.assertEqual((self.private / "drawing.bin").read_bytes(), b"private fixture")
        self.assertFalse(set(state["running"]) & {"nginx", "frontend", "api", "celery"})
        self.assertNotEqual(self.run_script("restore.sh", backup).returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

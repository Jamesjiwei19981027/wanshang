"""Atomic-backup metadata and strict integrity checks, with no shell extraction."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from media_archive import inspect_archive

FILES = ("database.dump", "media.tar.gz")
MARKER = ".axle-backup-root"


def regular(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Backup files must be regular files without links")


def digest(path: Path) -> str:
    regular(path)
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def prepare_root(path: Path) -> None:
    if path == Path(path.anchor):
        raise ValueError("A filesystem root cannot be a backup directory")
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            raise ValueError("Backup root must be a real directory")
        marker = path / MARKER
        if marker.exists():
            regular(marker)
            if marker.read_text() != "axle-production-backups-v1\n":
                raise ValueError("Unrecognized backup-root marker")
        elif next(path.iterdir(), None) is not None:
            raise ValueError("Refusing to repurpose a non-empty directory as a backup root")
    else:
        path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700)
    marker = path / MARKER
    if not marker.exists():
        with marker.open("x", encoding="utf-8") as output:
            output.write("axle-production-backups-v1\n")
        os.chmod(marker, 0o600)


def create(path: Path, project: str, repository: Path) -> dict:
    files = {}
    for name in FILES:
        target = path / name
        checksum = digest(target)
        if target.stat().st_size == 0:
            raise ValueError("Empty backup component")
        os.chmod(target, 0o600)
        files[name] = {"sha256": checksum, "size": target.stat().st_size}
    with (path / "database.dump").open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise ValueError("Expected a PostgreSQL custom-format dump")
    media = inspect_archive(path / "media.tar.gz")
    try:
        revision = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(repository), "status", "--porcelain"], stderr=subprocess.DEVNULL, text=True))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = "unavailable", None
    manifest = {"format": "axle-production-backup-v1", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "project": project, "revision": revision, "worktree_dirty": dirty,
                "consistency": "Application ingress, frontend, API and worker were stopped for this snapshot.",
                "files": files, "media": media}
    with (path / "manifest.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")
    with (path / "SHA256SUMS").open("x", encoding="ascii") as output:
        for name in FILES:
            output.write(files[name]["sha256"] + "  " + name + "\n")
    os.chmod(path / "manifest.json", 0o600)
    os.chmod(path / "SHA256SUMS", 0o600)
    return manifest


def verify(path: Path) -> dict:
    regular(path / "manifest.json")
    regular(path / "SHA256SUMS")
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "axle-production-backup-v1" or set(manifest.get("files", {})) != set(FILES):
        raise ValueError("Unrecognized backup format or file list")
    expected_lines = []
    for name in FILES:
        target = path / name
        expected = manifest["files"][name]
        if digest(target) != expected["sha256"] or target.stat().st_size != expected["size"]:
            raise ValueError("Backup integrity check failed: " + name)
        expected_lines.append(expected["sha256"] + "  " + name)
    if (path / "SHA256SUMS").read_text(encoding="ascii").splitlines() != expected_lines:
        raise ValueError("SHA256SUMS does not match the manifest")
    with (path / "database.dump").open("rb") as source:
        if source.read(5) != b"PGDMP":
            raise ValueError("Expected a PostgreSQL custom-format dump")
    media = inspect_archive(path / "media.tar.gz")
    if media != manifest.get("media"):
        raise ValueError("Media archive differs from the manifest")
    return {"backup_verified": True, "project": manifest["project"], "media": media}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create", "verify", "prepare-root"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--project", default="axle-production")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.directory.is_symlink():
        raise SystemExit("A backup directory must not be a symlink")
    path = args.directory.resolve()
    if args.action == "prepare-root":
        prepare_root(path)
        print(json.dumps({"backup_root_ready": True}))
    elif args.action == "create":
        print(json.dumps({"backup_manifest_created": True, "files": list(create(path, args.project, args.repository)["files"])}))
    else:
        print(json.dumps(verify(path)))


if __name__ == "__main__":
    main()

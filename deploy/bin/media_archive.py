"""Archive/restore only regular public/private media, with no link extraction."""
from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

ROOT_NAMES = ("public_media", "private_media")
DEFAULT_ROOTS = {"public_media": Path("/app/media"), "private_media": Path("/app/private_media")}
MAX_FILES = 100_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024 * 1024


def safe_member(member: tarfile.TarInfo, seen: dict[str, bool]) -> tuple[str, PurePosixPath]:
    name = member.name.rstrip("/")
    name.encode("utf-8", errors="strict")
    path = PurePosixPath(name)
    if (not name or name.startswith("/") or "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or any(part in ("", ".", "..") for part in name.split("/")) or str(path) != name
            or path.parts[0] not in ROOT_NAMES or name in seen):
        raise ValueError("Unsafe or duplicate archive path")
    directory = member.type == tarfile.DIRTYPE
    if not directory and member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
        raise ValueError("Links and non-regular archive entries are forbidden")
    if len(path.parts) == 1 and not directory:
        raise ValueError("Media roots must be directories")
    if member.size < 0 or member.size > MAX_FILE_BYTES or (directory and member.size):
        raise ValueError("Invalid or oversized media entry")
    for parent in path.parents:
        if str(parent) != "." and seen.get(str(parent)) is not True:
            raise ValueError("Archive parents must precede their children as directories")
    seen[name] = directory
    if len(seen) > MAX_FILES:
        raise ValueError("Too many media entries")
    return path.parts[0], path


def inspect_archive(path: Path) -> dict[str, int]:
    seen: dict[str, bool] = {}
    count = size = 0
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            safe_member(member, seen)
            if member.isfile():
                count += 1
                size += member.size
                if size > MAX_TOTAL_BYTES:
                    raise ValueError("Media restore exceeds the total size bound")
    if not all(seen.get(name) is True for name in ROOT_NAMES):
        raise ValueError("Archive is missing a media root")
    # Read through the gzip trailer as well, checking CRC and truncated streams.
    expanded = 0
    with gzip.open(path, "rb") as compressed:
        while chunk := compressed.read(1024 * 1024):
            expanded += len(chunk)
            if expanded > MAX_TOTAL_BYTES + MAX_FILES * 2048 + 1024 * 1024:
                raise ValueError("Expanded media archive exceeds its safety bound")
    return {"file_count": count, "total_file_bytes": size, "entry_count": len(seen)}


def check_roots(roots: dict[str, Path], *, empty: bool = False) -> None:
    for root in roots.values():
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Media roots must be real directories")
        if empty and next(root.iterdir(), None) is not None:
            raise ValueError("Restore refuses non-empty media volumes")


def dump(output, roots: dict[str, Path] = DEFAULT_ROOTS) -> None:
    check_roots(roots)
    entries = []
    total = 0
    for label, root in roots.items():
        pending = [(root, label)]
        while pending:
            path, name = pending.pop()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise ValueError("Media backups refuse links and non-regular files")
            if stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1 or metadata.st_size > MAX_FILE_BYTES:
                    raise ValueError("Linked or oversized media file")
                total += metadata.st_size
            entries.append((path, name, metadata))
            if len(entries) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ValueError("Media backup exceeds safety bounds")
            if stat.S_ISDIR(metadata.st_mode):
                pending.extend((child, name + "/" + child.name) for child in reversed(sorted(path.iterdir())))
    with tarfile.open(fileobj=output, mode="w|gz", format=tarfile.PAX_FORMAT) as archive:
        seen: dict[str, bool] = {}
        for path, name, metadata in entries:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE if stat.S_ISDIR(metadata.st_mode) else tarfile.REGTYPE
            info.mode = stat.S_IMODE(metadata.st_mode) & 0o777
            info.mtime = int(metadata.st_mtime)
            info.size = metadata.st_size if info.isfile() else 0
            safe_member(info, seen)
            if info.isdir():
                archive.addfile(info)
                continue
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as source:
                opened = os.fstat(source.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns):
                    raise ValueError("Media changed during the maintenance-window backup")
                archive.addfile(info, source)
                after = os.fstat(source.fileno())
                if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                    raise ValueError("Media changed while reading")


def restore(source, roots: dict[str, Path] = DEFAULT_ROOTS) -> dict[str, int]:
    check_roots(roots, empty=True)
    seen: dict[str, bool] = {}
    total = files = 0
    with tarfile.open(fileobj=source, mode="r|gz") as archive:
        for member in archive:
            label, relative = safe_member(member, seen)
            root = roots[label]
            target = root.joinpath(*relative.parts[1:])
            directory_mode, file_mode = ((0o755, 0o644) if label == "public_media" else (0o700, 0o600))
            if member.isdir():
                target.mkdir(mode=directory_mode, parents=True, exist_ok=True)
                if target.is_symlink() or not target.is_dir():
                    raise ValueError("Unsafe media directory during restore")
                os.chmod(target, directory_mode)
                continue
            total += member.size
            files += 1
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Media restore exceeds the total size bound")
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), file_mode)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("Missing media payload")
            with os.fdopen(descriptor, "wb") as output:
                remaining = member.size
                while remaining:
                    chunk = extracted.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Truncated media payload")
                    output.write(chunk)
                    remaining -= len(chunk)
    if not all(seen.get(name) is True for name in ROOT_NAMES):
        raise ValueError("Archive is missing a media root")
    return {"restored_file_count": files, "restored_bytes": total}


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "dump":
        dump(sys.stdout.buffer)
    elif action == "check-empty":
        check_roots(DEFAULT_ROOTS, empty=True)
        print(json.dumps({"media_volumes_empty": True}))
    elif action == "restore":
        print(json.dumps(restore(sys.stdin.buffer)))
    else:
        raise SystemExit("Expected dump, check-empty, or restore")

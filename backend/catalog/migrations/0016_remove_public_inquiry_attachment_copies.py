from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path

from django.conf import settings
from django.db import migrations


def _is_link_or_reparse(metadata) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(
        reparse_flag
        and getattr(metadata, "st_file_attributes", 0) & reparse_flag
    )


def _resolve_storage_root(root: Path) -> Path:
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return root.resolve()
    except OSError as exc:
        raise RuntimeError(
            f"Inquiry-attachment storage root could not be inspected safely: {root!s}."
        ) from exc
    if _is_link_or_reparse(metadata):
        raise RuntimeError(
            "Inquiry-attachment storage root is a link or reparse point: "
            f"{root!s}."
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError(
            f"Inquiry-attachment storage root is not a directory: {root!s}."
        )
    return root.resolve()


def _safe_storage_path(root: Path, relative_name: str) -> Path:
    if not isinstance(relative_name, str) or not relative_name:
        raise RuntimeError(
            f"Unsafe inquiry-attachment storage name: {relative_name!r}."
        )

    relative_path = Path(relative_name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeError(
            f"Unsafe inquiry-attachment storage name: {relative_name!r}."
        )

    resolved_root = _resolve_storage_root(root)
    candidate = resolved_root / relative_path
    current = resolved_root
    for part in relative_path.parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(
                "Inquiry-attachment storage path could not be inspected safely: "
                f"{relative_name!r}."
            ) from exc
        if _is_link_or_reparse(metadata):
            raise RuntimeError(
                "Inquiry-attachment storage path contains a link or reparse point: "
                f"{relative_name!r}."
            )

    resolved_path = candidate.resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise RuntimeError(
            "Inquiry-attachment path escapes its storage root: "
            f"{relative_name!r}."
        )
    return resolved_path


def _public_rfq_file_names(public_root: Path) -> list[str]:
    resolved_public_root = _resolve_storage_root(public_root)
    namespace = _safe_storage_path(
        resolved_public_root,
        "quarantine/rfq",
    )
    try:
        namespace_metadata = namespace.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise RuntimeError(
            "The public inquiry-attachment namespace could not be inspected safely."
        ) from exc
    if not stat.S_ISDIR(namespace_metadata.st_mode):
        raise RuntimeError(
            "The public inquiry-attachment namespace is not a regular directory."
        )

    file_names: list[str] = []
    directories = [namespace]
    while directories:
        directory = directories.pop()
        try:
            directory_metadata = directory.lstat()
        except OSError as exc:
            raise RuntimeError(
                "A public inquiry-attachment directory could not be inspected safely: "
                f"{directory!s}."
            ) from exc
        if _is_link_or_reparse(directory_metadata):
            raise RuntimeError(
                "The public inquiry-attachment namespace contains a link or "
                f"reparse point: {directory!s}."
            )
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise RuntimeError(
                "The public inquiry-attachment namespace contains a non-directory "
                f"where a directory was expected: {directory!s}."
            )

        child_directories: list[Path] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    relative_name = entry_path.relative_to(
                        resolved_public_root
                    ).as_posix()
                    try:
                        metadata = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise RuntimeError(
                            "A public inquiry-attachment entry could not be "
                            f"inspected safely: {relative_name!r}."
                        ) from exc

                    if _is_link_or_reparse(metadata):
                        raise RuntimeError(
                            "The public inquiry-attachment namespace contains a "
                            f"link or reparse point: {relative_name!r}."
                        )
                    if stat.S_ISDIR(metadata.st_mode):
                        child_directories.append(entry_path)
                    elif stat.S_ISREG(metadata.st_mode):
                        file_names.append(relative_name)
                    else:
                        raise RuntimeError(
                            "The public inquiry-attachment namespace contains a "
                            f"non-regular entry: {relative_name!r}."
                        )
        except RuntimeError:
            raise
        except OSError as exc:
            raise RuntimeError(
                "A public inquiry-attachment directory could not be enumerated "
                f"safely: {directory!s}."
            ) from exc

        directories.extend(
            sorted(child_directories, key=lambda path: path.name, reverse=True)
        )

    return sorted(file_names)


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _plan_legacy_attachment_cleanup(
    apps,
    public_root: Path,
    private_root: Path,
) -> tuple[dict[Path, Path], set[Path]]:
    resolved_public_root = _resolve_storage_root(public_root)
    resolved_private_root = _resolve_storage_root(private_root)
    if (
        resolved_public_root == resolved_private_root
        or resolved_public_root in resolved_private_root.parents
        or resolved_private_root in resolved_public_root.parents
    ):
        raise RuntimeError(
            "Public and private inquiry-attachment storage roots must not overlap."
        )

    InquiryAttachment = apps.get_model("catalog", "InquiryAttachment")
    planned_moves: dict[Path, Path] = {}
    duplicate_public_files: set[Path] = set()
    candidate_labels: dict[str, str] = {}
    missing: list[str] = []
    conflicts: list[str] = []

    for attachment in InquiryAttachment.objects.exclude(file="").iterator():
        relative_name = attachment.file.name
        if not isinstance(relative_name, str) or not relative_name:
            raise RuntimeError(
                f"Unsafe inquiry-attachment storage name: {relative_name!r}."
            )
        candidate_labels.setdefault(relative_name, f"pk={attachment.pk}")

    for relative_name in _public_rfq_file_names(resolved_public_root):
        candidate_labels.setdefault(relative_name, "orphan")

    for relative_name, label in candidate_labels.items():
        public_path = _safe_storage_path(resolved_public_root, relative_name)
        private_path = _safe_storage_path(resolved_private_root, relative_name)
        public_exists = public_path.exists()
        private_exists = private_path.exists()

        if public_exists and not public_path.is_file():
            conflicts.append(f"{label} ({relative_name}: public is not a file)")
            continue
        if private_exists and not private_path.is_file():
            conflicts.append(f"{label} ({relative_name}: private is not a file)")
            continue

        if public_exists and private_exists:
            if _digest(public_path) == _digest(private_path):
                duplicate_public_files.add(public_path)
            else:
                conflicts.append(f"{label} ({relative_name})")
        elif public_exists:
            planned_moves[public_path] = private_path
        elif not private_exists:
            missing.append(f"{label} ({relative_name})")

    if missing or conflicts:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing[:20]))
        if conflicts:
            details.append("conflicting copies: " + ", ".join(conflicts[:20]))
        raise RuntimeError(
            "Legacy inquiry-attachment files could not be cleaned safely; "
            + "; ".join(details)
        )

    return planned_moves, duplicate_public_files


def _cleanup_legacy_attachment_copies(
    apps,
    public_root: Path,
    private_root: Path,
) -> None:
    planned_moves, duplicate_public_files = _plan_legacy_attachment_cleanup(
        apps,
        public_root,
        private_root,
    )

    for public_path, private_path in planned_moves.items():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(public_path), str(private_path))

    # The private copy was verified byte-for-byte before any mutation, so the
    # public duplicate can now be removed without weakening the protected copy.
    for public_path in duplicate_public_files:
        public_path.unlink()


def remove_public_inquiry_attachment_copies(apps, schema_editor):
    _cleanup_legacy_attachment_copies(
        apps,
        Path(settings.MEDIA_ROOT),
        Path(settings.PRIVATE_MEDIA_ROOT),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0015_protect_product_document_files"),
    ]

    operations = [
        migrations.RunPython(
            remove_public_inquiry_attachment_copies,
            migrations.RunPython.noop,
        ),
    ]

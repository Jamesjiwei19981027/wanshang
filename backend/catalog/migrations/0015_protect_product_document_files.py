from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import catalog.file_security
import catalog.storage
from django.conf import settings
from django.db import migrations, models


def _safe_storage_path(root: Path, relative_name: str) -> Path:
    relative_path = Path(relative_name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise RuntimeError(
            f"Unsafe product-document storage name: {relative_name!r}."
        )
    resolved_root = root.resolve()
    resolved_path = (resolved_root / relative_path).resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise RuntimeError(
            f"Product-document path escapes its storage root: {relative_name!r}."
        )
    return resolved_path


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _relocate_documents(apps, source_root: Path, destination_root: Path) -> None:
    ProductDocument = apps.get_model("catalog", "ProductDocument")
    planned_moves: dict[Path, Path] = {}
    duplicate_sources: set[Path] = set()
    missing: list[str] = []
    conflicts: list[str] = []

    for document in ProductDocument.objects.exclude(file="").iterator():
        relative_name = document.file.name
        source = _safe_storage_path(source_root, relative_name)
        destination = _safe_storage_path(destination_root, relative_name)
        source_exists = source.is_file()
        destination_exists = destination.is_file()
        if source_exists and destination_exists:
            if _digest(source) != _digest(destination):
                conflicts.append(f"pk={document.pk} ({relative_name})")
            else:
                duplicate_sources.add(source)
        elif source_exists:
            planned_moves[source] = destination
        elif not destination_exists:
            missing.append(f"pk={document.pk} ({relative_name})")

    if missing or conflicts:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing[:20]))
        if conflicts:
            details.append("conflicting copies: " + ", ".join(conflicts[:20]))
        raise RuntimeError(
            "Product document files could not be moved safely; "
            + "; ".join(details)
        )

    for source, destination in planned_moves.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    # A verified protected copy already exists, so remove only the identical
    # public duplicate to close the raw /media/documents/ bypass.
    for source in duplicate_sources:
        source.unlink()


def move_documents_to_protected_storage(apps, schema_editor):
    _relocate_documents(
        apps,
        Path(settings.MEDIA_ROOT),
        Path(settings.PRIVATE_MEDIA_ROOT),
    )


def restore_documents_to_public_storage(apps, schema_editor):
    _relocate_documents(
        apps,
        Path(settings.PRIVATE_MEDIA_ROOT),
        Path(settings.MEDIA_ROOT),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0014_secure_public_external_urls"),
    ]

    operations = [
        migrations.RunPython(
            move_documents_to_protected_storage,
            restore_documents_to_public_storage,
        ),
        migrations.AlterField(
            model_name="productdocument",
            name="file",
            field=models.FileField(
                blank=True,
                storage=catalog.storage.PrivateMediaStorage(),
                upload_to="documents/%Y/%m/",
                validators=[
                    catalog.file_security.validate_product_document_upload,
                ],
            ),
        ),
    ]

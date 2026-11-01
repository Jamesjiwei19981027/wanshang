from __future__ import annotations

import logging
import os
import re
import shlex
# Required for a shell-free malware scanner argv.
import subprocess  # nosec B404
import warnings
from pathlib import Path
from typing import BinaryIO, TYPE_CHECKING

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

if TYPE_CHECKING:
    from .models import InquiryAttachment

logger = logging.getLogger(__name__)


class AttachmentSecurityError(ValueError):
    """Raised when an attachment's bytes do not match its claimed file type."""


EXPECTED_MIME_TYPES = {
    "pdf": {"application/pdf", "application/x-pdf", "application/octet-stream"},
    "dwg": {
        "application/acad",
        "application/autocad_dwg",
        "application/dwg",
        "application/octet-stream",
        "application/x-acad",
        "application/x-dwg",
        "image/vnd.dwg",
        "image/x-dwg",
    },
    "dxf": {
        "application/dxf",
        "application/octet-stream",
        "application/x-dxf",
        "image/vnd.dxf",
        "image/x-dxf",
        "text/plain",
    },
    "step": {"application/octet-stream", "application/step", "application/x-step", "model/step"},
    "stp": {"application/octet-stream", "application/step", "application/x-step", "model/step"},
    "png": {"application/octet-stream", "image/png"},
    "jpg": {"application/octet-stream", "image/jpeg", "image/pjpeg"},
    "jpeg": {"application/octet-stream", "image/jpeg", "image/pjpeg"},
    "webp": {"application/octet-stream", "image/webp"},
}


def _read_prefix(file_obj: BinaryIO, length: int = 4096) -> bytes:
    file_obj.seek(0)
    prefix = file_obj.read(length)
    file_obj.seek(0)
    return prefix


def validate_attachment_content(
    file_obj: BinaryIO,
    filename: str,
    declared_content_type: str = "",
) -> str:
    """Validate declared MIME and independent file signatures, then rewind."""
    extension = Path(filename).suffix.lower().lstrip(".")
    if extension not in settings.ALLOWED_UPLOAD_EXTENSIONS:
        raise AttachmentSecurityError("The file extension is not accepted.")

    declared_type = declared_content_type.partition(";")[0].strip().lower()
    expected_types = EXPECTED_MIME_TYPES.get(extension, {"application/octet-stream"})
    if declared_type and declared_type not in expected_types:
        raise AttachmentSecurityError(
            f"The declared MIME type {declared_type!r} does not match .{extension}."
        )

    try:
        prefix = _read_prefix(file_obj)
        if extension == "pdf":
            if not prefix.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").startswith(b"%PDF-"):
                raise AttachmentSecurityError("The file does not contain a valid PDF signature.")
        elif extension == "dwg":
            if not re.match(rb"^AC10[0-9]{2}", prefix):
                raise AttachmentSecurityError("The file does not contain a recognised DWG signature.")
        elif extension == "dxf":
            binary_signature = prefix.startswith(b"AutoCAD Binary DXF\r\n\x1a\x00")
            ascii_signature = re.search(
                rb"(?:^|\r?\n)\s*0\s*\r?\n\s*SECTION(?:\r?\n|$)",
                prefix,
                flags=re.IGNORECASE,
            )
            if not (binary_signature or ascii_signature):
                raise AttachmentSecurityError("The file does not contain a recognised DXF header.")
        elif extension in {"step", "stp"}:
            if not prefix.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").upper().startswith(b"ISO-10303-21;"):
                raise AttachmentSecurityError("The file does not contain a recognised STEP header.")
        elif extension in {"png", "jpg", "jpeg", "webp"}:
            expected_format = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}[extension]
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    # Select the permitted parser before it sees untrusted
                    # bytes; rejecting its detected type afterwards still
                    # lets a disguised file enter an unrelated codec.
                    with Image.open(file_obj, formats=[expected_format]) as image:
                        detected_format = image.format
                        image.verify()
            except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
                raise AttachmentSecurityError("The image dimensions are unsafe.") from exc
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
                raise AttachmentSecurityError("The file does not contain a valid image.") from exc
            if detected_format != expected_format:
                raise AttachmentSecurityError(
                    f"The image bytes are {detected_format or 'unknown'}, not {expected_format}."
                )
    finally:
        file_obj.seek(0)
    return extension


def validate_product_document_upload(file_obj: BinaryIO) -> None:
    """Validate a staff-uploaded public document before protected storage."""
    filename = str(getattr(file_obj, "name", ""))
    declared_content_type = str(getattr(file_obj, "content_type", ""))
    try:
        file_size = int(getattr(file_obj, "size", 0))
    except (TypeError, ValueError, OSError) as exc:
        raise DjangoValidationError(
            "The product document size could not be verified."
        ) from exc
    if file_size <= 0:
        raise DjangoValidationError("The product document is empty.")
    if file_size > settings.MAX_UPLOAD_BYTES:
        raise DjangoValidationError(
            "Product documents must not exceed the 10 MB per-file limit."
        )
    try:
        file_obj.open("rb")
        validate_attachment_content(
            file_obj,
            filename,
            declared_content_type,
        )
    except (AttachmentSecurityError, OSError, ValueError) as exc:
        raise DjangoValidationError(str(exc)) from exc


def _save_scan_result(attachment: "InquiryAttachment", status: str, detail: str) -> str:
    attachment.scan_status = status
    attachment.scan_detail = detail[:500]
    attachment.scanned_at = timezone.now()
    attachment.save(update_fields=["scan_status", "scan_detail", "scanned_at", "updated_at"])
    return status


def scan_inquiry_attachment(attachment: "InquiryAttachment") -> str:
    """
    Revalidate file signatures and run the configured ClamAV-compatible command.

    Scanner exit code 0 is clean, 1 is infected, and any other result is an
    operational error. Files remain in private quarantine unless marked clean.
    """
    from .models import InquiryAttachment

    try:
        with attachment.file.open("rb") as stored_file:
            validate_attachment_content(
                stored_file,
                attachment.original_name,
                attachment.declared_mime_type,
            )
    except Exception as exc:
        return _save_scan_result(
            attachment,
            InquiryAttachment.ScanStatus.ERROR,
            f"Signature validation failed: {exc}",
        )

    command = settings.ATTACHMENT_SCANNER_COMMAND.strip()
    if not command:
        if (
            settings.DJANGO_ENV != "production"
            and settings.ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT
        ):
            return _save_scan_result(
                attachment,
                InquiryAttachment.ScanStatus.CLEAN,
                "Development-only signature validation; no malware engine was configured.",
            )
        return _save_scan_result(
            attachment,
            InquiryAttachment.ScanStatus.ERROR,
            "No malware scanner is configured; download remains fail-closed.",
        )

    try:
        local_path = attachment.file.path
        arguments = shlex.split(command, posix=os.name != "nt")
        if not arguments:
            raise ValueError("Scanner command is empty.")
        # The executable/flags are trusted deployment configuration, the file
        # path is one appended argv value, and shell interpretation is disabled.
        completed = subprocess.run(  # nosec B603
            [*arguments, local_path],
            capture_output=True,
            check=False,
            text=True,
            timeout=settings.ATTACHMENT_SCAN_TIMEOUT,
        )
        scanner_output = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        if completed.returncode == 0:
            return _save_scan_result(
                attachment,
                InquiryAttachment.ScanStatus.CLEAN,
                scanner_output or "Malware scan completed successfully.",
            )
        if completed.returncode == 1:
            return _save_scan_result(
                attachment,
                InquiryAttachment.ScanStatus.INFECTED,
                scanner_output or "The malware scanner reported an infected file.",
            )
        return _save_scan_result(
            attachment,
            InquiryAttachment.ScanStatus.ERROR,
            scanner_output or f"Scanner exited with status {completed.returncode}.",
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return _save_scan_result(
            attachment,
            InquiryAttachment.ScanStatus.ERROR,
            f"Scanner execution failed: {exc}",
        )


def schedule_inquiry_attachment_scan(attachment: "InquiryAttachment") -> str:
    """
    Queue scanning without running the malware engine in an HTTP request.

    Queuing is deferred until after the database transaction commits so the
    worker can always see the attachment row. A broker outage keeps the file
    quarantined for the scan_attachments recovery command or an admin rescan.
    Running up to five scanner timeouts inline would exhaust request workers
    precisely when the queue is unavailable.
    """
    from django.db import transaction

    from .tasks import scan_inquiry_attachment_task

    attachment_pk = attachment.pk
    initial_status = attachment.scan_status

    def _queue_scan() -> None:
        try:
            scan_inquiry_attachment_task.delay(attachment_pk)
        except Exception:
            logger.exception(
                "Unable to queue attachment scan; file remains quarantined",
                extra={"attachment_id": attachment_pk},
            )
            from .models import InquiryAttachment

            # Publishing can fail after the broker accepted the task. Do not
            # overwrite a scan result already written by a fast worker.
            InquiryAttachment.objects.filter(
                pk=attachment_pk,
                scan_status=InquiryAttachment.ScanStatus.PENDING,
            ).update(
                scan_status=InquiryAttachment.ScanStatus.ERROR,
                scan_detail="Scan queue unavailable; attachment remains quarantined. Retry scanning after recovery.",
                updated_at=timezone.now(),
            )

    transaction.on_commit(_queue_scan)
    return initial_status

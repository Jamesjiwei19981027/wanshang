"""Uploaded image bytes cannot select parsers outside the accepted format."""

from io import BytesIO
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from .file_security import (
    AttachmentSecurityError,
    validate_attachment_content,
    validate_product_document_upload,
)
from .models import Inquiry, InquiryAttachment


def image_bytes(image_format):
    stream = BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format=image_format)
    return stream.getvalue()


class ImageDecoderSelectionTests(SimpleTestCase):
    def test_disguised_files_never_enter_other_image_parsers(self):
        Image.init()
        for image_format in ("TIFF", "GIF", "BMP", "JPEG"):
            with self.subTest(image_format=image_format):
                stream = BytesIO(image_bytes(image_format))
                original_factory, accept = Image.OPEN[image_format]
                decoder = Mock(wraps=original_factory)
                with patch.dict(Image.OPEN, {image_format: (decoder, accept)}):
                    with self.assertRaises(AttachmentSecurityError):
                        validate_attachment_content(stream, "drawing.png", "image/png")
                decoder.assert_not_called()
                self.assertFalse(stream.closed)
                self.assertEqual(stream.tell(), 0)

    def test_all_accepted_image_formats_remain_valid_and_are_rewound(self):
        for image_format, extension, mime in (
            ("PNG", "png", "image/png"),
            ("JPEG", "jpg", "image/jpeg"),
            ("JPEG", "jpeg", "image/jpeg"),
            ("WEBP", "webp", "image/webp"),
        ):
            with self.subTest(extension=extension):
                stream = BytesIO(image_bytes(image_format))
                stream.seek(3)
                self.assertEqual(
                    validate_attachment_content(stream, f"drawing.{extension}", mime),
                    extension,
                )
                self.assertFalse(stream.closed)
                self.assertEqual(stream.tell(), 0)

    def test_staff_document_validation_rejects_disguised_image_and_rewinds(self):
        upload = SimpleUploadedFile(
            "drawing.png", image_bytes("TIFF"), content_type="image/png",
        )
        with self.assertRaises(ValidationError):
            validate_product_document_upload(upload)
        self.assertFalse(upload.closed)
        self.assertEqual(upload.tell(), 0)


@override_settings(SECURE_SSL_REDIRECT=False)
class DisguisedImageEndpointTests(TestCase):
    def test_disguised_upload_is_400_without_decoding_or_persistence(self):
        Image.init()
        tiff_bytes = image_bytes("TIFF")
        original_factory, accept = Image.OPEN["TIFF"]
        for path in ("/api/inquiries/", "/api/v1/inquiries/"):
            with self.subTest(path=path):
                cache.clear()
                decoder = Mock(wraps=original_factory)
                with (
                    patch.dict(Image.OPEN, {"TIFF": (decoder, accept)}),
                    patch("catalog.serializers.verify_turnstile_token"),
                    patch("catalog.views.enqueue_inquiry_notification") as notify,
                ):
                    response = APIClient().post(path, {
                        "name": "Decoder boundary buyer",
                        "email": "decoder-test@example.com",
                        "privacy_consent": "true",
                        "attachments": SimpleUploadedFile(
                            "drawing.png", tiff_bytes, content_type="image/png",
                        ),
                    }, format="multipart")
                self.assertEqual(response.status_code, 400)
                self.assertIn("attachments", str(response.json()))
                decoder.assert_not_called()
                notify.assert_not_called()
                self.assertFalse(Inquiry.objects.exists())
                self.assertFalse(InquiryAttachment.objects.exists())

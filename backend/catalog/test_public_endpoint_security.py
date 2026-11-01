from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .file_security import schedule_inquiry_attachment_scan
from .models import AnalyticsEvent, Category, Inquiry, InquiryAttachment, Product, ProductDocument
from .tests import isolated_attachment_storage


@override_settings(SECURE_SSL_REDIRECT=False)
class PublicDocumentFilterSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        category = Category.objects.create(name="Filter security", slug="filter-security")
        cls.product = Product.objects.create(
            category=category,
            title="Unicode model",
            slug="unicode-model",
            model="\u00b2",
            sku="FILTER-SECURITY",
            status=Product.Status.PUBLISHED,
        )
        cls.document = ProductDocument.objects.create(
            product=cls.product,
            title="Public specification",
            document_type=ProductDocument.DocumentType.DRAWING,
            external_url="https://cdn.example.com/specification.pdf",
        )

    def test_non_decimal_unicode_digits_are_treated_as_model_names(self):
        # str.isdigit() accepts superscripts which int() cannot parse. These
        # public filters must not turn an ordinary query into a server error.
        for path in ("/api/documents/", "/api/v1/documents/", "/api/downloads/"):
            with self.subTest(path=path):
                response = self.client.get(path, {"product": self.product.model})
                self.assertEqual(response.status_code, 200)
                body = response.json()
                documents = body["data"] if isinstance(body, dict) else body
                self.assertEqual([item["id"] for item in documents], [self.document.pk])

    def test_numeric_ids_and_overflowing_ids_remain_safe(self):
        response = self.client.get("/api/documents/", {"product": str(self.product.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["id"], self.document.pk)
        for product_id in ("0", str(2**63), "9" * 100):
            with self.subTest(product_id=product_id):
                response = self.client.get("/api/documents/", {"product": product_id})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), [])

    def test_control_characters_are_rejected_before_any_database_query(self):
        for path in ("/api/documents/", "/api/downloads/",
                     "/api/v1/documents/", "/api/v1/downloads/"):
            for product in ("model\x00unsafe", "model\tunsafe", "model\nunsafe", "model\x7funsafe"):
                with self.subTest(path=path, product=repr(product)), self.assertNumQueries(0):
                    response = self.client.get(path, {"product": product})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("product", str(response.json()))

    def test_product_text_filters_reject_nul_before_any_database_query(self):
        # These selectors have separate syntax/choice validators. Keep their
        # database boundary covered alongside the free-form document model.
        for path in ("/api/products/", "/api/v1/products/"):
            for field in ("q", "category", "application", "axle_type", "type",
                          "brake_type", "brake", "bolt_pattern", "finish",
                          "tube_shape", "capacity_unit", "dimension_unit",
                          "ordering", "sort"):
                with self.subTest(path=path, field=field), self.assertNumQueries(0):
                    response = self.client.get(path, {field: "value\x00unsafe"})
                    self.assertEqual(response.status_code, 400)


@override_settings(SECURE_SSL_REDIRECT=False)
class PublicProductReferenceSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_explicit_product_references_are_bounded_before_database_lookup(self):
        for path in ("/api/events/", "/api/page-views/"):
            for product_id in (0, -1, 2**63, "9" * 100):
                with self.subTest(path=path, product_id=product_id):
                    response = self.client.post(path, {
                        "event_name": "view_product", "path": "/products/",
                        "session_id": "reference-security-session", "product_id": product_id,
                    }, format="json")
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("product_id", response.json())

    def test_invalid_metadata_product_references_are_ignored_without_database_errors(self):
        for product_id in (0, -1, 2**63, "9" * 100, 1e300, True, [1], {"id": 1}):
            with self.subTest(product_id=product_id):
                response = self.client.post("/api/events/", {
                    "event_name": "view_product", "path": "/products/",
                    "metadata": {"product_id": product_id},
                }, format="json")
                self.assertEqual(response.status_code, 201)
                self.assertIsNone(AnalyticsEvent.objects.order_by("-pk").first().product_id)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    ATTACHMENT_SCANNER_COMMAND="clamscan --no-summary",
    ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT=False,
)
class AttachmentQueueOutageSecurityTests(TestCase):
    def test_publish_error_does_not_overwrite_a_completed_worker_scan(self):
        inquiry = Inquiry.objects.create(name="Race buyer", email="race@example.com")
        attachment = InquiryAttachment.objects.create(
            inquiry=inquiry, file="quarantine/race.pdf",
            original_name="race.pdf", size=16,
        )
        scanned_at = timezone.now()

        def accepted_then_disconnected(attachment_id):
            InquiryAttachment.objects.filter(pk=attachment_id).update(
                scan_status=InquiryAttachment.ScanStatus.CLEAN,
                scan_detail="Clean worker result.", scanned_at=scanned_at,
            )
            raise OSError("publisher disconnected after broker acceptance")

        with (
            patch("catalog.tasks.scan_inquiry_attachment_task.delay", side_effect=accepted_then_disconnected),
            self.assertLogs("catalog.file_security", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            schedule_inquiry_attachment_scan(attachment)
        attachment.refresh_from_db()
        self.assertEqual(attachment.scan_status, InquiryAttachment.ScanStatus.CLEAN)
        self.assertEqual(attachment.scan_detail, "Clean worker result.")
        self.assertEqual(attachment.scanned_at, scanned_at)

    def test_broker_outage_quarantines_files_without_scanning_in_request(self):
        cache.clear()
        self.enterContext(isolated_attachment_storage())
        client = APIClient()
        with (
            patch("catalog.serializers.verify_turnstile_token"),
            patch("catalog.views.enqueue_inquiry_notification"),
            patch("catalog.tasks.scan_inquiry_attachment_task.delay", side_effect=OSError("broker unavailable")),
            patch("catalog.file_security.scan_inquiry_attachment") as synchronous_scan,
            self.assertLogs("catalog.file_security", level="ERROR"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = client.post("/api/inquiries/", {
                "name": "Queue outage buyer",
                "email": "queue-outage@example.com",
                "privacy_consent": "true",
                "attachments": [
                    SimpleUploadedFile(f"drawing-{index}.pdf", b"%PDF-1.7\n%%EOF\n", content_type="application/pdf")
                    for index in range(5)
                ],
            }, format="multipart")
        self.assertEqual(response.status_code, 201)
        synchronous_scan.assert_not_called()
        attachments = list(InquiryAttachment.objects.all())
        self.assertEqual(len(attachments), 5)
        for attachment in attachments:
            self.assertEqual(attachment.scan_status, InquiryAttachment.ScanStatus.ERROR)
            self.assertIn("queue", attachment.scan_detail.lower())
            self.assertIsNone(attachment.scanned_at)

        admin = get_user_model().objects.create_superuser(username="queue-recovery-admin", email="admin@example.com")
        client.force_login(admin)
        download_url = reverse("inquiry-attachment-download", kwargs={"pk": attachments[0].pk})
        self.assertEqual(client.get(download_url).status_code, 423)

        # The existing recovery command can process the retained files after
        # the outage, without asking the buyer to submit the RFQ a second time.
        with patch("catalog.file_security.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="OK", stderr="")) as scanner:
            call_command("scan_attachments", stdout=StringIO())
        self.assertEqual(scanner.call_count, 5)
        self.assertEqual(InquiryAttachment.objects.filter(scan_status=InquiryAttachment.ScanStatus.CLEAN).count(), 5)

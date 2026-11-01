from contextlib import contextmanager
import bz2
from datetime import timedelta
from decimal import Decimal
import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import json
import zlib
from urllib.parse import parse_qs

from django.contrib import admin as django_admin
from django.contrib.admin.models import LogEntry
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import call, patch

from django.test import TestCase, override_settings
from rest_framework import serializers as drf_serializers
from rest_framework.test import APIClient

from .admin import ContactMessageAdmin, InquiryAdmin, InquiryFollowupInline
from .spec_templates import TRAILER_AXLE_PARAMETERS, apply_template
from .specs import refresh_search_document
from .models import (
    AnalyticsEvent,
    Application,
    Banner,
    Category,
    ContactMessage,
    Inquiry,
    InquiryAttachment,
    InquiryItem,
    InquiryFollowup,
    PageView,
    Product,
    ProductDocument,
    ProductImage,
    NewsArticle,
    SearchMiss,
    SearchSynonym,
    SiteSetting,
    SpecField,
    ProductSpecValue,
    VisitSession,
)
from .serializers import (
    AnalyticsEventSerializer,
    ApplicationSerializer,
    BannerSerializer,
    CategorySerializer,
    InquirySerializer,
    ProductImageSerializer,
    ProductListSerializer,
    NewsArticleListSerializer,
    SiteSettingSerializer,
    VisitSessionSerializer,
)
from .throttles import (
    AnalyticsEventThrottle,
    AttachmentUploadIpThrottle,
    AttributionThrottle,
    ContactIpThrottle,
    RFQIpThrottle,
)
from .tasks import (
    QUEUE_ERROR_PREFIX,
    _claim_delivery,
    _release_delivery,
    scan_inquiry_attachment_task,
    send_contact_notification,
    send_inquiry_notification,
)
from .turnstile import TurnstileVerificationError, verify_turnstile_token


def _eager_scan_attachment(attachment_id, *args, **kwargs):
    """Run the Celery scan task inline so tests observe the scan result."""
    return scan_inquiry_attachment_task.apply(args=[attachment_id])


@contextmanager
def isolated_attachment_storage():
    field = InquiryAttachment._meta.get_field("file")
    original_storage = field.storage
    with TemporaryDirectory() as directory:
        field.storage = FileSystemStorage(location=directory)
        try:
            yield Path(directory)
        finally:
            field.storage = original_storage


@contextmanager
def isolated_product_document_storage():
    field = ProductDocument._meta.get_field("file")
    original_storage = field.storage

    class ProtectedTestStorage(FileSystemStorage):
        def url(self, name):
            raise ValueError("Protected files do not have public URLs.")

    with TemporaryDirectory() as directory:
        field.storage = ProtectedTestStorage(location=directory)
        try:
            yield Path(directory)
        finally:
            field.storage = original_storage


@contextmanager
def isolated_product_image_storage():
    field = ProductImage._meta.get_field("image")
    original_storage = field.storage
    with TemporaryDirectory() as directory:
        field.storage = FileSystemStorage(location=directory, base_url="/media/")
        try:
            yield Path(directory)
        finally:
            field.storage = original_storage


def close_streaming_file(response) -> None:
    """Release FileResponse handles without emitting request_finished in TestCase."""
    for closer in list(response._resource_closers):
        closer()
    response._resource_closers.clear()


def make_axle_category(slug="trailer-axles", name="Trailer Axles", **extra) -> Category:
    category = Category.objects.create(name=name, slug=slug, **extra)
    apply_template(category, TRAILER_AXLE_PARAMETERS)
    return category


def set_specs(product: Product, values: dict) -> None:
    """values: {key: "text" | (number, unit) | bool}."""
    fields = {field.key: field for field in product.category.effective_spec_fields()}
    for key, value in values.items():
        spec = ProductSpecValue(product=product, field=fields[key])
        if isinstance(value, tuple):
            spec.number_value, spec.entered_unit = Decimal(str(value[0])), value[1]
        elif isinstance(value, bool):
            spec.boolean_value = value
        else:
            spec.text_value = value
        spec.full_clean()
        spec.save()
    refresh_search_document(product.pk)


class PublicApiTests(TestCase):
    def setUp(self):
        cache.clear()
        category = make_axle_category()
        self.product = Product.objects.create(
            category=category,
            title="Test axle",
            slug="test-axle",
            model="TEST-3500",
            sku="TEST-3500",
            short_description="Test product",
            packaging="Export pallet",
            moq="20 sets",
            status=Product.Status.PUBLISHED,
        )
        set_specs(
            self.product,
            {
                "rated_capacity": (3500, "lb"),
                "axle_type": "Straight",
                "brake_type": "Electric drum",
                "bolt_pattern": "5 on 4.5",
                "stud_size": "1/2-20 UNF",
                "hub_face": (95, "in"),
                "spring_center": (80, "in"),
            },
        )
        self.client = APIClient()

    def specs_of(self, payload: dict) -> dict:
        return {row["key"]: row for row in payload["specs"]}

    def test_product_list_serializes_parameters_in_both_unit_systems(self):
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        payload = response.data["results"][0]
        specs = self.specs_of(payload)
        self.assertEqual(specs["rated_capacity"]["display"], {"imperial": "3,500 lb", "metric": "1,588 kg"})
        self.assertEqual(specs["hub_face"]["display"], {"imperial": "95 in", "metric": "2,413 mm"})
        self.assertEqual(specs["brake_type"]["display"]["imperial"], "Electric drum")
        self.assertTrue(specs["rated_capacity"]["show_on_card"])
        # Buyer-only RFQ questions never appear on products.
        self.assertNotIn("trailer_weight", specs)
        self.assertEqual(payload["moq"], "20 sets")
        self.assertEqual(payload["packaging"], "Export pallet")

    def test_numeric_filters_compare_values_entered_in_different_units(self):
        metric = Product.objects.create(
            category=self.product.category, title="Metric axle", slug="metric-axle", model="M-1600", sku="M-1600",
            short_description="Metric", status=Product.Status.PUBLISHED,
        )
        set_specs(metric, {"rated_capacity": (1600, "kg"), "hub_face": (2413, "mm")})

        def slugs(params):
            response = self.client.get("/api/products/", params)
            self.assertEqual(response.status_code, 200, response.content)
            return sorted(item["slug"] for item in response.data["results"])

        # 1,600 kg is about 3,527 lb: both products are at least 3,000 lb.
        self.assertEqual(slugs({"spec.rated_capacity__min": "3000"}), ["metric-axle", "test-axle"])
        self.assertEqual(slugs({"spec.rated_capacity__min": "3510", "spec.rated_capacity__unit": "lb"}), ["metric-axle"])
        self.assertEqual(slugs({"spec.rated_capacity__max": "1590", "spec.rated_capacity__unit": "kg"}), ["test-axle"])
        self.assertEqual(slugs({"spec.rated_capacity__min": "1500", "units": "metric"}), ["metric-axle", "test-axle"])
        # 95 in equals 2,413 mm: the same hub face whatever unit was entered.
        self.assertEqual(slugs({"spec.hub_face": "95"}), ["metric-axle", "test-axle"])
        self.assertEqual(slugs({"spec.hub_face": "2413", "spec.hub_face__unit": "mm"}), ["metric-axle", "test-axle"])
        self.assertEqual(slugs({"spec.hub_face": "2413mm"}), ["metric-axle", "test-axle"])
        self.assertEqual(slugs({"spec.rated_capacity__min": "1590 kg"}), ["metric-axle"])

    def test_choice_and_text_filters_match_case_insensitively(self):
        def count(params):
            response = self.client.get("/api/products/", params)
            self.assertEqual(response.status_code, 200)
            return response.data["count"]

        self.assertEqual(count({"spec.brake_type": "electric drum"}), 1)
        self.assertEqual(count({"spec.brake_type": "Hydraulic disc"}), 0)
        self.assertEqual(count({"spec.bolt_pattern": "5 ON 4.5"}), 1)
        self.assertEqual(count({"spec.axle_type": "Straight", "spec.rated_capacity__min": "3000"}), 1)

    def test_parameter_filters_reject_unknown_unfilterable_and_malformed_values(self):
        for params in (
            {"spec.not_a_parameter": "x"},
            {"spec.inner_bearing": "L68149"},  # exists but is not a filter
            {"spec.rated_capacity__min": "lots"},
            {"spec.rated_capacity__min": "NaN"},
            {"spec.rated_capacity__unit": "mm", "spec.rated_capacity__min": "1"},
            {"spec.rated_capacity__min": "95 mm"},
            {"spec.rated_capacity__min": "5000", "spec.rated_capacity__max": "100"},
            {"spec.brake_type__min": "1"},
            {"spec.bad key": "1"},
            {"units": "nautical"},
            {"ordering": "capacity_asc"},
        ):
            with self.subTest(params=params):
                response = self.client.get("/api/products/", params)
                self.assertEqual(response.status_code, 400)

    def test_product_pagination_rejects_malformed_or_excessive_pages(self):
        for page in ("", "Infinity", "1.5", "0", "-1", "10001", "9" * 100):
            with self.subTest(page=page):
                response = self.client.get("/api/products/", {"page": page})
                self.assertEqual(response.status_code, 400)
                self.assertIn("page", response.data)

        first_page = self.client.get("/api/products/", {"page": "1"})
        self.assertEqual(first_page.status_code, 200)

    def test_filter_options_list_filterable_parameters_with_values(self):
        response = self.client.get("/api/filters/", {"category": "trailer-axles"})
        self.assertEqual(response.status_code, 200)
        options = {entry["key"]: entry for entry in response.data["parameters"]}
        self.assertIn("rated_capacity", options)
        self.assertEqual(options["rated_capacity"]["values"]["imperial"], ["3500"])
        self.assertEqual(options["rated_capacity"]["values"]["metric"], ["1588"])
        self.assertEqual(options["brake_type"]["values"], ["Electric drum"])
        # Filterable parameters without any product value are omitted.
        self.assertNotIn("tube_shape", options)
        self.assertNotIn("inner_bearing", options)
        self.assertEqual(self.client.get("/api/filters/", {"category": "missing"}).status_code, 400)

    def test_subcategories_inherit_parameters_and_appear_under_their_parent(self):
        child = Category.objects.create(name="American-Type Axles", slug="american-type-axles", parent=self.product.category)
        SpecField.objects.create(category=child, key="hub_brand", label="Hub brand", data_type=SpecField.DataType.TEXT, filterable=True)
        keys = [field.key for field in child.effective_spec_fields()]
        self.assertIn("rated_capacity", keys)
        self.assertIn("hub_brand", keys)
        child_product = Product.objects.create(
            category=child, title="American axle", slug="american-axle", model="AM-1", sku="AM-1",
            short_description="American type", status=Product.Status.PUBLISHED,
        )
        set_specs(child_product, {"rated_capacity": (5200, "lb"), "hub_brand": "Dexter-style"})
        parent_listing = self.client.get("/api/products/", {"category": "trailer-axles"})
        self.assertEqual(sorted(item["slug"] for item in parent_listing.data["results"]), ["american-axle", "test-axle"])
        child_listing = self.client.get("/api/products/", {"category": "american-type-axles", "spec.hub_brand": "dexter-style"})
        self.assertEqual([item["slug"] for item in child_listing.data["results"]], ["american-axle"])
        detail = self.client.get("/api/categories/american-type-axles/").data
        self.assertEqual([item["slug"] for item in detail["path"]], ["trailer-axles", "american-type-axles"])
        self.assertIn("rated_capacity", [field["key"] for field in detail["rfq_fields"]])

        self.product.category.parent = child
        with self.assertRaises(ValidationError):
            self.product.category.full_clean()
        child.inherit_parent_specs = False
        child.save()
        self.assertEqual([field.key for field in child.effective_spec_fields()], ["hub_brand"])

    def test_search_splits_words_reads_units_and_tolerates_typos(self):
        other = Product.objects.create(
            category=self.product.category, title="Heavy hydraulic disc axle", slug="heavy-axle", model="HD-7000", sku="HD-7000",
            short_description="Heavy duty", status=Product.Status.PUBLISHED,
        )
        set_specs(other, {"rated_capacity": (3175, "kg"), "brake_type": "Hydraulic disc"})
        boat = Application.objects.create(name="Boat Trailers", slug="boat-trailers")
        hidden = Application.objects.create(name="Secret Mining Application", slug="secret-mining", is_active=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.product.applications.set([boat, hidden])

        def search(query):
            response = self.client.get("/api/products/", {"q": query})
            self.assertEqual(response.status_code, 200)
            return [item["slug"] for item in response.data["results"]]

        # Words need not be adjacent or in order.
        self.assertEqual(search("electric axle test"), ["test-axle"])
        self.assertEqual(search("boat trailers"), ["test-axle"])
        self.assertEqual(search("5 on 4.5"), ["test-axle"])
        # 7,000 lb equals the 3,175 kg entry.
        self.assertEqual(search("7000 lb"), ["heavy-axle"])
        self.assertEqual(search("3500lb electric"), ["test-axle"])
        self.assertEqual(search("1588 kg"), ["test-axle"])
        # One wrong letter still finds the product.
        self.assertEqual(search("hydrualic"), ["heavy-axle"])
        # An exact model number ranks first.
        self.assertEqual(search("HD-7000")[0], "heavy-axle")
        self.assertEqual(search("Secret Mining"), [])

    def test_admin_synonyms_expand_search_terms(self):
        with self.captureOnCommitCallbacks(execute=True):
            SearchSynonym.objects.create(terms="running gear, axle")
        response = self.client.get("/api/products/", {"q": "running gear"})
        self.assertEqual([item["slug"] for item in response.data["results"]], ["test-axle"])

    def test_site_search_endpoint_covers_products_documents_and_news(self):
        ProductDocument.objects.create(
            product=self.product, title="Installation torque chart", document_type=ProductDocument.DocumentType.INSTALLATION,
            external_url="https://cdn.example.com/torque.pdf",
        )
        NewsArticle.objects.create(
            title="Choosing a torque wrench", slug="torque-news", summary="How to tighten wheel nuts.",
            status=NewsArticle.Status.PUBLISHED,
        )
        response = self.client.get("/api/search/", {"q": "torque"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["title"] for item in response.data["documents"]], ["Installation torque chart"])
        self.assertEqual([item["slug"] for item in response.data["news"]], ["torque-news"])
        self.assertTrue(response.data["documents"][0]["url"].endswith(f"/api/documents/{ProductDocument.objects.get().pk}/download/"))
        self.assertEqual(self.client.get("/api/search/", {"q": "x" * 121}).status_code, 400)

    def test_consenting_visitors_record_searches_without_results(self):
        response = self.client.post(
            "/api/events/",
            {"event_name": "search_product", "path": "/search", "metadata": {"query": "Landing Leg  crank", "results": 0, "query_length": 18}},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SearchMiss.objects.get().query, "landing leg crank")
        self.assertNotIn("query", AnalyticsEvent.objects.get().metadata)
        for query in ("buyer@example.com", "call 13800138000"):
            self.client.post("/api/events/", {"event_name": "search_product", "metadata": {"query": query, "results": 0}}, format="json")
        self.assertEqual(SearchMiss.objects.count(), 1)

    def test_made_to_order_filter_is_independent(self):
        self.product.is_customizable = False
        self.product.made_to_order = True
        self.product.save(update_fields=["is_customizable", "made_to_order"])

        response = self.client.get("/api/products/?made_to_order=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["slug"] for item in response.data["results"]], [self.product.slug])
        self.assertTrue(response.data["results"][0]["made_to_order"])
        customizable = self.client.get("/api/products/?customizable=true")
        self.assertEqual(customizable.data["count"], 0)
        invalid = self.client.get("/api/products/?made_to_order=sometimes")
        self.assertEqual(invalid.status_code, 400)

    def test_navigation_hides_applications_until_one_is_published(self):
        items = [item["url"] for item in self.client.get("/api/navigation/").data["items"]]
        self.assertNotIn("/applications", items)
        Application.objects.create(name="Utility", slug="utility")
        items = [item["url"] for item in self.client.get("/api/navigation/").data["items"]]
        self.assertIn("/applications", items)

    def test_category_detail_exposes_seo_and_hides_inactive_category(self):
        category = self.product.category
        category.meta_title = "Trailer axle catalogue"
        category.meta_description = "Select trailer axles by capacity and fitment."
        category.save(update_fields=["meta_title", "meta_description"])

        detail = self.client.get(f"/api/v1/categories/{category.slug}/")
        self.assertEqual(detail.status_code, 200)
        data = detail.json()["data"]
        self.assertEqual(data["meta_title"], category.meta_title)
        self.assertEqual(data["meta_description"], category.meta_description)
        self.assertEqual(data["product_count"], 1)

        category.is_active = False
        category.save(update_fields=["is_active"])
        hidden = self.client.get(f"/api/v1/categories/{category.slug}/")
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.json()["error_code"], "NOT_FOUND")

    def test_inactive_applications_are_hidden_from_all_public_product_surfaces(self):
        active = Application.objects.create(
            name="Utility Trailers",
            slug="utility-trailers",
        )
        inactive = Application.objects.create(
            name="Retired Application",
            slug="retired-application",
            is_active=False,
        )
        self.product.applications.set([active, inactive])
        self.product.featured = True
        self.product.save(update_fields=["featured"])

        applications = self.client.get("/api/applications/")
        self.assertEqual(
            [item["slug"] for item in applications.data],
            [active.slug],
        )
        self.assertEqual(
            self.client.get(f"/api/applications/{inactive.slug}/").status_code,
            404,
        )

        product_list = self.client.get("/api/products/")
        self.assertEqual(
            [item["slug"] for item in product_list.data["results"][0]["applications"]],
            [active.slug],
        )
        product_detail = self.client.get(f"/api/products/{self.product.slug}/")
        self.assertEqual(
            [item["slug"] for item in product_detail.data["applications"]],
            [active.slug],
        )
        home = self.client.get("/api/home/")
        self.assertEqual(
            [item["slug"] for item in home.data["applications"]],
            [active.slug],
        )
        self.assertEqual(
            [
                item["slug"]
                for item in home.data["featured_products"][0]["applications"]
            ],
            [active.slug],
        )

        inactive_filter = self.client.get(
            f"/api/products/?application={inactive.slug}"
        )
        self.assertEqual(inactive_filter.data["count"], 0)
    def test_health_endpoint_checks_database_and_cache(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")

    def test_v1_contract_endpoints_use_documented_envelope(self):
        endpoints = [
            "/api/v1/site-settings/",
            "/api/v1/navigation/",
            "/api/v1/home/",
            "/api/v1/product-categories/",
            "/api/v1/products/",
            f"/api/v1/products/{self.product.slug}/",
            f"/api/v1/products/{self.product.slug}/related/",
            "/api/v1/product-filter-options/",
            "/api/v1/applications/",
            "/api/v1/news/",
            "/api/v1/downloads/",
        ]
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                response = self.client.get(endpoint)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertTrue(payload["success"])
                self.assertIn("data", payload)
                self.assertIn("message", payload)

        product_page = self.client.get("/api/v1/products/").json()["data"]
        self.assertEqual(product_page["total"], 1)
        self.assertEqual(product_page["page"], 1)
        self.assertEqual(product_page["page_size"], 12)
        self.assertEqual(product_page["total_pages"], 1)

        invalid = self.client.get("/api/v1/products/?spec.rated_capacity__min=invalid")
        self.assertEqual(invalid.status_code, 400)
        error = invalid.json()
        self.assertFalse(error["success"])
        self.assertEqual(error["error_code"], "VALIDATION_ERROR")
        self.assertIn("spec.rated_capacity__min", error["fields"])

        non_finite = self.client.get("/api/v1/products/?spec.rated_capacity__min=NaN")
        self.assertEqual(non_finite.status_code, 400)
        self.assertFalse(non_finite.json()["success"])

        missing_route = self.client.get("/api/v1/not-a-route/")
        self.assertEqual(missing_route.status_code, 404)
        self.assertEqual(missing_route.json()["error_code"], "NOT_FOUND")

    def test_v1_pagination_reports_actual_last_page_number(self):
        for index in range(12):
            Product.objects.create(
                category=self.product.category,
                title=f"Paged axle {index}",
                slug=f"paged-axle-{index}",
                model=f"PAGED-{index}",
                sku=f"PAGED-{index}",
                short_description="Pagination envelope test",
                status=Product.Status.PUBLISHED,
            )

        response = self.client.get("/api/v1/products/?page=last")

        self.assertEqual(response.status_code, 200)
        page = response.json()["data"]
        self.assertEqual(page["page"], 2)
        self.assertEqual(page["total"], 13)
        self.assertEqual(page["total_pages"], 2)

    def test_v1_application_detail_returns_documented_envelope(self):
        application = Application.objects.create(
            name="Utility Trailers",
            slug="utility-trailers",
            summary="Utility application",
        )
        self.product.applications.add(application)
        detail = self.client.get(f"/api/v1/applications/{application.slug}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["data"]["slug"], application.slug)

    def test_home_hides_featured_products_from_inactive_categories(self):
        self.product.featured = True
        self.product.save(update_fields=["featured"])
        self.product.category.is_active = False
        self.product.category.save(update_fields=["is_active"])

        response = self.client.get("/api/home/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["featured_products"], [])

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_multipart_rfq_keeps_product_snapshot_apart_from_buyer_requirements(self, mocked_task):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/inquiries/",
                {
                    "name": "Technical Buyer",
                    "email": "buyer@example.com",
                    "message": "Please quote this configuration.",
                    "privacy_consent": "true",
                    "items": json.dumps([{"product_id": self.product.id, "quantity": 2}]),
                    "requested_categories": json.dumps(["trailer-axles"]),
                    "specifications": json.dumps({"trailer-axles": {"hub_face": "2413 mm", "spring_center": "80 in"}}),
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, 201, response.content)
        inquiry = Inquiry.objects.get(reference=response.data["reference"])
        item = inquiry.items.get()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.product_snapshot["specs"]["rated_capacity"]["value"], "3,500 lb")
        self.assertEqual(item.product_snapshot["specs"]["rated_capacity"]["metric"], "1,588 kg")
        self.assertEqual(inquiry.requested_categories, ["trailer-axles"])
        requirement = inquiry.specifications["trailer-axles"]
        self.assertEqual(requirement["hub_face"]["value"], "2,413 mm")
        self.assertEqual(requirement["hub_face"]["label"], "Hub face")
        self.assertNotIn("rated_capacity", requirement)
        mocked_task.assert_called_once_with(inquiry.pk)

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_normalizes_category_parameters_and_general_requirements(self, mocked_task):
        Category.objects.create(name="Trailer Landing Gear", slug="landing-gear")
        specifications = {
            "trailer-axles": {
                "rated_capacity": "3,500",
                "trailer_weight": " 10,000 ",
                "axle_count": "tandem",
                "brake_type": "hydraulic disc",
                "operating_environment": " saltwater ",
                "tube_shape": "round",
                "drop_height": "4 inch",
            },
            "general": {
                "oem_requirement": "YES",
                "odm_requirement": "discuss",
                "special_requirements": "  Confirm salt-spray target.  ",
                "selected_product": f" {self.product.model} ",
            },
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                "/api/inquiries/",
                {
                    "name": "Structured Buyer",
                    "email": "structured@example.com",
                    "privacy_consent": True,
                    "requested_categories": ["trailer-axles", "landing-gear"],
                    "specifications": specifications,
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        inquiry = Inquiry.objects.get(reference=response.data["reference"])
        axle = inquiry.specifications["trailer-axles"]
        self.assertEqual(axle["rated_capacity"]["value"], "3,500 lb")
        self.assertEqual(axle["trailer_weight"]["value"], "10,000 lb")
        self.assertEqual(axle["axle_count"]["value"], "Tandem")
        self.assertEqual(axle["brake_type"]["value"], "Hydraulic disc")
        self.assertEqual(axle["operating_environment"]["value"], "saltwater")
        self.assertEqual(axle["drop_height"]["value"], "4 in")
        general = inquiry.specifications["general"]
        self.assertEqual(general["oem_requirement"], "yes")
        self.assertEqual(general["special_requirements"], "Confirm salt-spray target.")
        self.assertEqual(general["selected_product"], self.product.model)
        self.assertEqual(inquiry.requested_categories, ["trailer-axles", "landing-gear"])

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_rejects_unknown_nested_nonfinite_and_invalid_specs(self, mocked_task):
        invalid_cases = (
            {"trailer-axles": {"unknown_internal_key": "value"}},
            {"trailer-axles": {"inner_bearing": "L68149"}},
            {"trailer-axles": {"operating_environment": ["nested"]}},
            {"trailer-axles": {"rated_capacity": "NaN"}},
            {"trailer-axles": {"rated_capacity": "95 mm"}},
            {"trailer-axles": {"axle_count": "quad"}},
            {"trailer-axles": {"brake_type": "unsafe"}},
            {"trailer-axles": {"hub_face": "95 lb"}},
            {"trailer-axles": {"operating_environment": "x" * 161}},
            {"trailer-axles": {"operating_environment": f"unsafe{chr(0)}value"}},
            {"trailer-axles": {"operating_environment": f"unsafe{chr(0xD800)}value"}},
            {"landing-gear": {"rated_capacity": "1"}},
            {"general": {"oem_requirement": "maybe"}},
            {"general": {"special_requirements": "x" * 4001}},
            {"general": {"internal_note": "x"}},
            {"trailer-axles": "not an object"},
        )
        for specifications in invalid_cases:
            with self.subTest(specifications=str(specifications)[:80]):
                cache.clear()
                request_body = json.dumps(
                    {
                        "name": "Invalid Specs Buyer",
                        "email": "invalid-specs@example.com",
                        "privacy_consent": True,
                        "requested_categories": ["trailer-axles"],
                        "specifications": specifications,
                    },
                    ensure_ascii=True,
                )
                response = self.client.post("/api/inquiries/", request_body, content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertIn("specifications", response.data)
        self.assertFalse(Inquiry.objects.filter(email="invalid-specs@example.com").exists())
        mocked_task.assert_not_called()

    def test_rfq_rejects_unknown_or_inactive_categories(self):
        Category.objects.create(name="Retired", slug="retired", is_active=False)
        for categories in (["missing"], ["retired"], "trailer-axles", [1], ["c" + str(index) for index in range(11)]):
            with self.subTest(categories=categories):
                response = self.client.post(
                    "/api/inquiries/",
                    {"name": "Buyer", "email": "categories@example.com", "privacy_consent": True, "requested_categories": categories},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("requested_categories", response.data)

    def test_rfq_application_must_be_a_published_application_or_other(self):
        Application.objects.create(name="Boat Trailers", slug="boat-trailers")
        Application.objects.create(name="Retired", slug="retired-application", is_active=False)
        serializer = InquirySerializer()
        self.assertEqual(serializer.validate_application("boat-trailers"), "boat-trailers")
        self.assertEqual(serializer.validate_application("other"), "other")
        self.assertEqual(serializer.validate_application(""), "")
        for value in ("retired-application", "utility-trailers"):
            with self.subTest(value=value), self.assertRaises(drf_serializers.ValidationError):
                serializer.validate_application(value)

    def test_rfq_item_json_rejects_unsafe_nonfinite_and_deep_values(self):
        validator = InquirySerializer().validate_items
        deeply_nested: object = "leaf"
        for _ in range(10):
            deeply_nested = {"nested": deeply_nested}
        invalid_values = (
            {"note": f"unsafe{chr(0)}value"},
            {"note": f"unsafe{chr(0xD800)}value"},
            {"number": float("nan")},
            {"nested": deeply_nested},
        )
        for selected_options in invalid_values:
            with self.subTest(selected_options=repr(selected_options)):
                with self.assertRaises(drf_serializers.ValidationError):
                    validator(
                        [
                            {
                                "product_id": self.product.pk,
                                "quantity": 1,
                                "selected_options": selected_options,
                                "requested_specs": {},
                            }
                        ]
                    )

        for unsafe_text in (f"unsafe{chr(0)}value", f"unsafe{chr(0xD800)}value"):
            with self.subTest(http_unsafe_text=repr(unsafe_text)):
                cache.clear()
                request_body = json.dumps(
                    {
                        "name": "Unsafe Item Buyer",
                        "email": "unsafe-item@example.com",
                        "privacy_consent": True,
                        "items": [
                            {
                                "product_id": self.product.pk,
                                "quantity": 1,
                                "selected_options": {"note": unsafe_text},
                                "requested_specs": {},
                            }
                        ],
                    },
                    ensure_ascii=True,
                )
                response = self.client.post(
                    "/api/inquiries/",
                    request_body,
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("items", response.data)

    def test_rfq_item_identifiers_and_quantities_use_strict_positive_integers(self):
        validator = InquirySerializer().validate_items
        normalized = validator(
            [
                {
                    "product_id": str(self.product.pk),
                    "quantity": "2",
                    "selected_options": {},
                    "requested_specs": {},
                }
            ]
        )
        self.assertEqual(normalized[0]["product_id"], self.product.pk)
        self.assertEqual(normalized[0]["quantity"], 2)

        invalid_pairs = (
            (True, 1),
            (self.product.pk, True),
            (float(self.product.pk), 1),
            (self.product.pk, 1.9),
            (f"+{self.product.pk}", 1),
            (self.product.pk, "+1"),
            (f"{self.product.pk}.0", 1),
            (self.product.pk, "1.0"),
            (f" {self.product.pk}", 1),
            (self.product.pk, "1 "),
            (0, 1),
            (self.product.pk, 0),
            (9_223_372_036_854_775_808, 1),
            (self.product.pk, 100_001),
        )
        for product_id, quantity in invalid_pairs:
            with self.subTest(product_id=product_id, quantity=quantity):
                with self.assertRaises(drf_serializers.ValidationError):
                    validator(
                        [
                            {
                                "product_id": product_id,
                                "quantity": quantity,
                                "selected_options": {},
                                "requested_specs": {},
                            }
                        ]
                    )

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_http_rejects_non_integer_item_values(self, mocked_task):
        invalid_pairs = (
            (True, 1),
            (self.product.pk, True),
            (float(self.product.pk), 1),
            (self.product.pk, 1.9),
            (f"+{self.product.pk}", 1),
            (self.product.pk, "1.0"),
        )
        for index, (product_id, quantity) in enumerate(invalid_pairs):
            with self.subTest(product_id=product_id, quantity=quantity):
                cache.clear()
                response = self.client.post(
                    "/api/inquiries/",
                    {
                        "name": "Strict Integer Buyer",
                        "email": f"strict-integer-{index}@example.com",
                        "privacy_consent": True,
                        "items": [
                            {
                                "product_id": product_id,
                                "quantity": quantity,
                                "selected_options": {},
                                "requested_specs": {},
                            }
                        ],
                    },
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("items", response.data)
        self.assertFalse(
            Inquiry.objects.filter(email__startswith="strict-integer-").exists()
        )
        mocked_task.assert_not_called()

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_rejects_unknown_products(self, mocked_task):
        response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Technical Buyer",
                "email": "buyer@example.com",
                "privacy_consent": True,
                "items": [{"product_id": 999_999, "quantity": 1}],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Inquiry.objects.count(), 0)
        mocked_task.assert_not_called()

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_rejects_product_from_inactive_category(self, mocked_task):
        self.product.category.is_active = False
        self.product.category.save(update_fields=["is_active"])

        response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Technical Buyer",
                "email": "buyer@example.com",
                "privacy_consent": True,
                "items": [{"product_id": self.product.pk, "quantity": 1}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("items", response.data)
        self.assertEqual(Inquiry.objects.count(), 0)
        mocked_task.assert_not_called()

    def test_public_forms_reject_invalid_phone_application_and_item_json(self):
        invalid_rfq = self.client.post(
            "/api/inquiries/",
            {
                "name": "Buyer",
                "email": "invalid-rfq@example.com",
                "phone": "call me maybe",
                "application": "unsupported-application",
                "privacy_consent": True,
                "items": [
                    {
                        "product_id": self.product.pk,
                        "selected_options": ["not", "an", "object"],
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(invalid_rfq.status_code, 400)
        self.assertIn("phone", invalid_rfq.data)
        self.assertIn("application", invalid_rfq.data)
        self.assertIn("items", invalid_rfq.data)

        invalid_contact = self.client.post(
            "/api/contact/",
            {
                "name": "Buyer",
                "email": "invalid-contact@example.com",
                "phone": "<script>",
                "message": "Please contact me.",
                "privacy_consent": True,
            },
            format="json",
        )
        self.assertEqual(invalid_contact.status_code, 400)
        self.assertIn("phone", invalid_contact.data)

    def test_rfq_rejects_zero_quantity_and_non_object_specifications(self):
        response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Buyer",
                "email": "invalid-specs@example.com",
                "quantity": 0,
                "specifications": ["not", "an", "object"],
                "privacy_consent": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("quantity", response.data)
        self.assertIn("specifications", response.data)
        self.assertEqual(Inquiry.objects.count(), 0)

    def test_catalogue_paginates_without_hiding_total_count(self):
        for index in range(12):
            Product.objects.create(
                category=self.product.category,
                title=f"Additional axle {index}",
                slug=f"additional-axle-{index}",
                model=f"MODEL-{index}",
                sku=f"SKU-{index}",
                short_description="Pagination test",
                status=Product.Status.PUBLISHED,
            )
        response = self.client.get("/api/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 13)
        self.assertEqual(len(response.data["results"]), 12)
        self.assertIsNotNone(response.data["next"])

    def test_inactive_and_unpublished_documents_are_not_exposed(self):
        inactive = ProductDocument.objects.create(
            product=self.product,
            title="Internal revision",
            document_type=ProductDocument.DocumentType.DRAWING,
            external_url="https://example.com/internal.pdf",
            is_active=False,
        )
        detail = self.client.get(f"/api/products/{self.product.slug}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["documents"], [])

        inactive.is_active = True
        inactive.save(update_fields=["is_active"])
        self.product.status = Product.Status.DRAFT
        self.product.save(update_fields=["status"])
        listing = self.client.get("/api/documents/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data, [])

    def test_document_download_is_counted(self):
        document = ProductDocument.objects.create(
            product=self.product,
            title="Published drawing",
            document_type=ProductDocument.DocumentType.DRAWING,
            external_url="https://example.com/drawing.pdf",
            is_active=True,
        )
        response = self.client.get(f"/api/documents/{document.pk}/download/")
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.downloads, 1)
        preview = self.client.get(
            f"/api/documents/{document.pk}/download/?preview=1"
        )
        self.assertEqual(preview.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.downloads, 1)
        listing = self.client.get(
            f"/api/documents/?product={self.product.slug}&format=PDF&type=drawing"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(len(listing.data), 1)
        self.assertEqual(listing.data[0]["product_id"], self.product.pk)
        self.assertEqual(listing.data[0]["file_format"], "PDF")
        self.assertIsNone(listing.data[0]["file_size"])
        self.assertIn("updated_at", listing.data[0])

    def test_document_download_fails_closed_for_unvalidated_legacy_url(self):
        document = ProductDocument.objects.create(
            product=self.product,
            title="Unsafe imported drawing",
            document_type=ProductDocument.DocumentType.DRAWING,
            external_url="ftp://user:secret@evil.example/payload.pdf",
            is_active=True,
        )

        for suffix in ("", "?preview=1"):
            with self.subTest(suffix=suffix):
                response = self.client.get(
                    f"/api/documents/{document.pk}/download/{suffix}"
                )
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response)

        document.refresh_from_db()
        self.assertEqual(document.downloads, 0)

    def test_unsafe_external_document_preview_is_rejected_without_counting(self):
        document = ProductDocument.objects.create(
            product=self.product,
            title="External CAD drawing",
            document_type=ProductDocument.DocumentType.DRAWING,
            external_url="https://example.com/drawing.dwg",
            is_active=True,
        )
        response = self.client.get(
            f"/api/documents/{document.pk}/download/?preview=1"
        )
        self.assertEqual(response.status_code, 400)
        document.refresh_from_db()
        self.assertEqual(document.downloads, 0)

        detail = self.client.get(f"/api/products/{self.product.slug}/")
        self.assertFalse(detail.data["documents"][0]["previewable"])

    def test_empty_document_returns_404_without_incrementing_downloads(self):
        document = ProductDocument.objects.create(
            product=self.product,
            title="Misconfigured drawing",
            document_type=ProductDocument.DocumentType.DRAWING,
            is_active=True,
        )
        response = self.client.get(f"/api/documents/{document.pk}/download/")
        self.assertEqual(response.status_code, 404)
        document.refresh_from_db()
        self.assertEqual(document.downloads, 0)

    def test_browser_safe_document_preview_is_inline_and_not_counted_as_download(self):
        with isolated_product_document_storage():
            document = ProductDocument.objects.create(
                product=self.product,
                title="Published PDF",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=SimpleUploadedFile(
                    "published.pdf",
                    b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                is_active=True,
            )
            response = self.client.get(
                f"/api/documents/{document.pk}/download/?preview=1"
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response["Content-Disposition"].startswith("inline;"))
            self.assertEqual(response["Content-Type"], "application/pdf")
            document.refresh_from_db()
            self.assertEqual(document.downloads, 0)
            detail = self.client.get(f"/api/products/{self.product.slug}/")
            self.assertEqual(
                detail.data["documents"][0]["file_size"],
                len(b"%PDF-1.4\n%%EOF\n"),
            )
            close_streaming_file(response)

            downloaded = self.client.get(
                f"/api/documents/{document.pk}/download/"
            )
            self.assertEqual(downloaded.status_code, 200)
            self.assertTrue(
                downloaded["Content-Disposition"].startswith("attachment;")
            )
            document.refresh_from_db()
            self.assertEqual(document.downloads, 1)
            close_streaming_file(downloaded)

    def test_product_document_uploads_reject_active_content_and_spoofed_files(self):
        cases = (
            SimpleUploadedFile(
                "payload.html",
                b"<script>alert(1)</script>",
                content_type="text/html",
            ),
            SimpleUploadedFile(
                "spoofed.pdf",
                b"<script>alert(1)</script>",
                content_type="application/pdf",
            ),
        )
        for uploaded_file in cases:
            with self.subTest(filename=uploaded_file.name):
                document = ProductDocument(
                    product=self.product,
                    title="Unsafe product document",
                    document_type=ProductDocument.DocumentType.DRAWING,
                    file=uploaded_file,
                    is_active=True,
                )
                with self.assertRaises(ValidationError):
                    document.full_clean()

    def test_product_document_bytes_have_no_raw_url_and_legacy_html_fails_closed(self):
        with isolated_product_document_storage():
            document = ProductDocument.objects.create(
                product=self.product,
                title="Legacy active content",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=SimpleUploadedFile(
                    "payload.html",
                    b"<script>alert(1)</script>",
                    content_type="text/html",
                ),
                is_active=True,
            )

            with self.assertRaises(ValueError):
                _ = document.file.url
            with patch("catalog.views.logger.exception"):
                response = self.client.get(
                    f"/api/documents/{document.pk}/download/"
                )
            self.assertEqual(response.status_code, 404)
            document.refresh_from_db()
            self.assertEqual(document.downloads, 0)

    def test_shared_protected_document_file_survives_until_last_reference_is_deleted(self):
        with isolated_product_document_storage():
            first = ProductDocument.objects.create(
                product=self.product,
                title="Shared drawing A",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=SimpleUploadedFile(
                    "shared.pdf",
                    b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                is_active=True,
            )
            second = ProductDocument.objects.create(
                product=self.product,
                title="Shared drawing B",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=first.file.name,
                is_active=True,
            )
            stored_path = Path(first.file.path)

            with self.captureOnCommitCallbacks(execute=True):
                first.delete()
            self.assertTrue(stored_path.exists())
            downloaded = self.client.get(
                f"/api/documents/{second.pk}/download/"
            )
            self.assertEqual(downloaded.status_code, 200)
            close_streaming_file(downloaded)

            with self.captureOnCommitCallbacks(execute=True):
                second.delete()
            self.assertFalse(stored_path.exists())

    def test_replacing_protected_document_removes_unreferenced_old_bytes(self):
        with isolated_product_document_storage():
            document = ProductDocument.objects.create(
                product=self.product,
                title="Replaceable drawing",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=SimpleUploadedFile(
                    "old.pdf",
                    b"%PDF-1.4\nold\n%%EOF\n",
                    content_type="application/pdf",
                ),
                is_active=True,
            )
            old_path = Path(document.file.path)
            document.file = SimpleUploadedFile(
                "new.pdf",
                b"%PDF-1.4\nnew\n%%EOF\n",
                content_type="application/pdf",
            )
            with self.captureOnCommitCallbacks(execute=True):
                document.save(update_fields=["file", "updated_at"])
            new_path = Path(document.file.path)

            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())

    def test_unsafe_preview_extension_remains_an_attachment(self):
        with isolated_product_document_storage():
            document = ProductDocument.objects.create(
                product=self.product,
                title="Published CAD",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=SimpleUploadedFile(
                    "published.dwg",
                    b"AC1032\x00drawing",
                    content_type="application/octet-stream",
                ),
                is_active=True,
            )
            response = self.client.get(
                f"/api/documents/{document.pk}/download/?preview=1"
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response["Content-Disposition"].startswith("attachment;"))
            document.refresh_from_db()
            self.assertEqual(document.downloads, 1)
            close_streaming_file(response)

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_long_source_url_is_validated_and_saved(self, mocked_task):
        source_url = f"https://example.com/{'a' * 430}"
        response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Technical Buyer",
                "email": "long-url@example.com",
                "privacy_consent": True,
                "source_url": source_url,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Inquiry.objects.get(reference=response.data["reference"]).source_url, source_url)

    def test_invalid_image_content_is_rejected(self):
        response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Technical Buyer",
                "email": "bad-image@example.com",
                "privacy_consent": "true",
                "attachments": SimpleUploadedFile("drawing.png", b"not-an-image", content_type="image/png"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("attachments", response.data)

    def test_spoofed_pdf_cad_and_mime_types_are_rejected(self):
        cases = [
            SimpleUploadedFile(
                "drawing.pdf",
                b"<script>alert(1)</script>",
                content_type="application/pdf",
            ),
            SimpleUploadedFile(
                "drawing.dwg",
                b"<script>alert(1)</script>",
                content_type="application/octet-stream",
            ),
            SimpleUploadedFile(
                "drawing.pdf",
                b"%PDF-1.4\n%%EOF\n",
                content_type="text/html",
            ),
        ]
        for index, attachment in enumerate(cases):
            with self.subTest(attachment=attachment.name, index=index):
                response = self.client.post(
                    "/api/inquiries/",
                    {
                        "name": "Technical Buyer",
                        "email": f"spoof-{index}@example.com",
                        "privacy_consent": "true",
                        "attachments": attachment,
                    },
                    format="multipart",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("attachments", response.data)

    @patch(
        "catalog.views.send_inquiry_notification.delay",
        side_effect=RuntimeError("broker unavailable"),
    )
    def test_notification_queue_failure_is_persisted(self, mocked_task):
        with patch("catalog.tasks.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/inquiries/",
                    {
                        "name": "Technical Buyer",
                        "email": "queue-failure@example.com",
                        "privacy_consent": True,
                    },
                    format="json",
                )
        self.assertEqual(response.status_code, 201)
        inquiry = Inquiry.objects.get(reference=response.data["reference"])
        self.assertIsNone(inquiry.notification_queued_at)
        self.assertTrue(inquiry.notification_error.startswith(QUEUE_ERROR_PREFIX))
        mocked_task.assert_called_once_with(inquiry.pk)

    @patch(
        "catalog.views.send_contact_notification.delay",
        side_effect=RuntimeError("broker unavailable"),
    )
    def test_contact_notification_queue_failure_is_persisted(self, mocked_task):
        with patch("catalog.tasks.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/contact/",
                    {
                        "name": "Technical Buyer",
                        "email": "contact-queue-failure@example.com",
                        "message": "Please contact me.",
                        "privacy_consent": True,
                    },
                    format="json",
                )
        self.assertEqual(response.status_code, 201)
        self.assertRegex(response.data["reference"], r"^MSG-\d{6}-[0-9A-F]{12}$")
        self.assertEqual(response.data["message"], "Your message has been received.")
        message = ContactMessage.objects.get()
        self.assertEqual(message.reference, response.data["reference"])
        self.assertIsNone(message.notification_queued_at)
        self.assertTrue(message.notification_error.startswith(QUEUE_ERROR_PREFIX))
        mocked_task.assert_called_once_with(message.pk)

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_ip_limit_cannot_be_bypassed_by_rotating_email(self, mocked_task):
        for index in range(5):
            response = self.client.post(
                "/api/inquiries/",
                {
                    "name": "Automated Buyer",
                    "email": f"rotating-{index}@example.com",
                    "privacy_consent": True,
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201)
        blocked = self.client.post(
            "/api/inquiries/",
            {
                "name": "Automated Buyer",
                "email": "rotating-final@example.com",
                "privacy_consent": True,
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 429)

    def test_inquiry_attachment_storage_has_no_public_url(self):
        attachment = InquiryAttachment(
            inquiry=Inquiry(name="Buyer", email="buyer@example.com"),
            original_name="drawing.pdf",
            size=100,
        )
        attachment.file.name = "rfq/example/drawing.pdf"
        with self.assertRaises(ValueError):
            attachment.file.url


class AttributionApiTests(TestCase):
    def setUp(self):
        cache.clear()
        category = Category.objects.create(
            name="Session products",
            slug="session-products",
        )
        self.product = Product.objects.create(
            category=category,
            title="Session axle",
            slug="session-axle",
            model="SESSION-3500",
            sku="SESSION-3500",
            short_description="Attribution test product",
            status=Product.Status.PUBLISHED,
        )
        self.client = APIClient()
        self.session_key = "session-abc123"

    def test_visit_session_preserves_first_touch_and_updates_last_touch(self):
        created = self.client.post(
            "/api/visit-sessions/",
            {
                "session_id": self.session_key,
                "country": "US",
                "city": "Austin",
                "first_touch_source": "google",
                "last_touch_source": "google",
                "utm_source": "google",
                "utm_medium": "cpc",
                "utm_campaign": "axle-launch",
                "utm_term": "trailer axle",
                "utm_content": "hero-a",
                "referrer_url": "https://www.google.com/search?q=axle",
                "first_landing_page": "/products/session-axle?utm_source=google",
                "last_landing_page": "/products/session-axle?utm_source=google",
                "device_type": "desktop",
                "browser": "Chrome",
                "os": "Windows",
                "consent_status": "accepted",
            },
            format="json",
            REMOTE_ADDR="198.51.100.21",
            HTTP_USER_AGENT="Attribution Browser/1.0",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            created.data,
            {"message": "Session activity recorded."},
        )
        session = VisitSession.objects.get(session_key=self.session_key)
        self.assertEqual(session.first_touch_source, "google")
        self.assertEqual(session.last_touch_source, "google")
        self.assertEqual(session.utm_term, "trailer axle")
        self.assertEqual(session.utm_content, "hero-a")
        self.assertNotEqual(session.first_ip_hash, "198.51.100.21")
        self.assertEqual(len(session.first_ip_hash), 64)

        updated = self.client.post(
            "/api/visit-sessions/",
            {
                "session_id": self.session_key,
                "first_touch_source": "tampered-first-touch",
                "last_touch_source": "email",
                "utm_source": "tampered-source",
                "last_landing_page": "/contact",
            },
            format="json",
            REMOTE_ADDR="203.0.113.8",
        )
        self.assertEqual(updated.status_code, 201)
        self.assertEqual(
            updated.data,
            {"message": "Session activity recorded."},
        )
        session.refresh_from_db()
        self.assertEqual(session.first_touch_source, "google")
        self.assertEqual(session.utm_source, "google")
        self.assertEqual(session.last_touch_source, "email")
        self.assertEqual(session.last_landing_page, "/contact")
        self.assertNotEqual(session.first_ip_hash, "203.0.113.8")

    def test_known_session_key_never_discloses_existing_attribution(self):
        session = VisitSession.objects.create(
            session_key=self.session_key,
            first_ip_hash="private-ip-hash",
            country="US",
            city="Austin",
            first_touch_source="private-first-touch",
            last_touch_source="private-last-touch",
            utm_source="private-source",
            utm_medium="private-medium",
            utm_campaign="private-campaign",
            utm_term="private-term",
            utm_content="private-content",
            referrer_url="https://private.example/referrer",
            first_landing_page="/private-first-page",
            last_landing_page="/private-last-page",
            user_agent="Private Browser/1.0",
            device_type="private-device",
            browser="private-browser",
            os="private-os",
            consent_status=VisitSession.ConsentStatus.ACCEPTED,
        )
        self.assertTrue(
            VisitSessionSerializer().fields["session_key"].write_only
        )
        self.assertNotIn(
            "session_key",
            VisitSessionSerializer(session).data,
        )
        second_anonymous_client = APIClient()

        response = second_anonymous_client.post(
            "/api/visit-sessions/",
            {"session_key": self.session_key},
            format="json",
            REMOTE_ADDR="203.0.113.99",
            HTTP_USER_AGENT="Unrelated Browser/2.0",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data,
            {"message": "Session activity recorded."},
        )
        disclosed_fields = set(VisitSessionSerializer.Meta.fields).intersection(
            response.data
        )
        self.assertEqual(disclosed_fields, set())

        versioned_response = second_anonymous_client.post(
            "/api/v1/visit-sessions/",
            {"session_id": self.session_key},
            format="json",
            REMOTE_ADDR="203.0.113.99",
            HTTP_USER_AGENT="Unrelated Browser/2.0",
        )
        self.assertEqual(versioned_response.status_code, 201)
        self.assertEqual(
            versioned_response.json(),
            {
                "success": True,
                "data": {},
                "message": "Session activity recorded.",
            },
        )
        self.assertEqual(
            set(VisitSessionSerializer.Meta.fields).intersection(
                versioned_response.json()["data"]
            ),
            set(),
        )

    def test_page_view_and_supported_event_link_to_visit_session(self):
        page_view_response = self.client.post(
            "/api/page-views/",
            {
                "session_id": self.session_key,
                "path": "/products/session-axle",
                "page_title": "Session axle",
                "product_id": self.product.pk,
            },
            format="json",
            REMOTE_ADDR="198.51.100.22",
        )
        self.assertEqual(page_view_response.status_code, 201)
        page_view = PageView.objects.get()
        self.assertEqual(page_view.session.session_key, self.session_key)
        self.assertEqual(page_view.product, self.product)
        self.assertEqual(page_view.session.first_landing_page, "/products/session-axle")
        self.assertEqual(page_view.session.last_landing_page, "/products/session-axle")
        self.assertNotEqual(AttributionThrottle.scope, AnalyticsEventThrottle.scope)

        event_response = self.client.post(
            "/api/events/",
            {
                "session_id": self.session_key,
                "event_name": "whatsapp_click",
                "path": "/products/session-axle",
                "metadata": {
                    "placement": "sticky-contact",
                    "product_id": self.product.pk,
                    "server_side": True,
                },
            },
            format="json",
            REMOTE_ADDR="198.51.100.22",
        )
        self.assertEqual(event_response.status_code, 201)
        event = AnalyticsEvent.objects.get()
        self.assertEqual(event.visit_session, page_view.session)
        self.assertEqual(event.event_name, "click_whatsapp")
        self.assertEqual(event.product, self.product)
        self.assertEqual(event.metadata["placement"], "sticky-contact")
        self.assertNotIn("server_side", event.metadata)
        forged_conversion = self.client.post(
            "/api/events/",
            {
                "session_id": self.session_key,
                "event_name": "submit_contact",
                "path": "/contact",
                "metadata": {"server_side": True},
            },
            format="json",
        )
        self.assertEqual(forged_conversion.status_code, 400)
        self.assertEqual(AnalyticsEvent.objects.count(), 1)

        metadata_validator = AnalyticsEventSerializer().validate_metadata
        unsafe_metadata_values = (
            {"note": f"unsafe{chr(0)}value"},
            {"note": f"unsafe{chr(0xD800)}value"},
            {"number": float("inf")},
        )
        for metadata in unsafe_metadata_values:
            with self.subTest(metadata=repr(metadata)):
                with self.assertRaises(drf_serializers.ValidationError):
                    metadata_validator(metadata)

    @patch("catalog.views.send_inquiry_notification.delay")
    def test_rfq_links_complete_session_attribution_and_conversion(self, mocked_task):
        self.client.post(
            "/api/visit-sessions/",
            {
                "session_id": self.session_key,
                "first_touch_source": "google",
                "last_touch_source": "linkedin",
                "utm_source": "google",
                "utm_medium": "cpc",
                "utm_campaign": "axle-launch",
                "utm_term": "heavy axle",
                "utm_content": "drawing-cta",
                "referrer_url": "https://www.google.com/search?q=heavy+axle",
                "first_landing_page": "/products/session-axle",
                "last_landing_page": "/rfq",
            },
            format="json",
            REMOTE_ADDR="198.51.100.23",
        )
        response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Attributed Buyer",
                "email": "attributed@example.com",
                "privacy_consent": True,
                "session_id": self.session_key,
                "source_url": "https://example.com/rfq",
                "last_touch_source": "linkedin",
                "destination_port": "Long Beach",
                "target_delivery_date": "2026-10-15",
                "items": [
                    {
                        "product_id": self.product.pk,
                        "quantity": 2,
                        "selected_options": {"brake": "electric"},
                        "requested_specs": {"hub_face": "95 in"},
                    }
                ],
            },
            format="json",
            REMOTE_ADDR="198.51.100.23",
            HTTP_REFERER="https://example.com/products/session-axle",
            HTTP_USER_AGENT="RFQ Browser/2.0",
        )
        self.assertEqual(response.status_code, 201)
        inquiry = Inquiry.objects.get(reference=response.data["reference"])
        session = VisitSession.objects.get(session_key=self.session_key)
        self.assertEqual(inquiry.session, session)
        self.assertEqual(inquiry.first_touch_source, "google")
        self.assertEqual(inquiry.last_touch_source, "linkedin")
        self.assertEqual(inquiry.utm_term, "heavy axle")
        self.assertEqual(inquiry.utm_content, "drawing-cta")
        self.assertEqual(
            inquiry.referrer,
            "https://www.google.com/search?q=heavy+axle",
        )
        self.assertEqual(
            inquiry.first_landing_page,
            "/products/session-axle",
        )
        self.assertEqual(inquiry.last_landing_page, "https://example.com/rfq")
        self.assertEqual(inquiry.user_agent, "RFQ Browser/2.0")
        self.assertEqual(len(inquiry.ip_hash), 64)
        self.assertEqual(inquiry.destination_port, "Long Beach")
        self.assertEqual(str(inquiry.target_delivery_date), "2026-10-15")
        self.assertEqual(
            inquiry.items.get().selected_options,
            {"brake": "electric"},
        )
        self.assertEqual(
            inquiry.items.get().requested_specs,
            {"hub_face": "95 in"},
        )
        conversion_event = AnalyticsEvent.objects.get(
            event_name="submit_inquiry",
        )
        self.assertTrue(conversion_event.metadata["server_side"])
        self.assertEqual(conversion_event.product, self.product)
        session.refresh_from_db()
        self.assertIsNotNone(session.converted_at)

    @patch("catalog.views.send_contact_notification.delay")
    def test_contact_links_session_and_term_content(self, mocked_task):
        response = self.client.post(
            "/api/contact/",
            {
                "name": "Service Buyer",
                "email": "service@example.com",
                "message": "Please contact our service team.",
                "privacy_consent": True,
                "session_id": self.session_key,
                "source_url": "https://example.com/contact",
                "first_touch_source": "newsletter",
                "last_touch_source": "newsletter",
                "utm_source": "newsletter",
                "utm_medium": "email",
                "utm_term": "service parts",
                "utm_content": "footer-link",
                "referrer_url": "https://mail.example.net/campaign",
                "first_landing_page": "/contact?utm_source=newsletter",
                "last_landing_page": "/contact?utm_source=newsletter",
            },
            format="json",
            HTTP_USER_AGENT="Contact Browser/1.0",
        )
        self.assertEqual(response.status_code, 201)
        message = ContactMessage.objects.get()
        self.assertEqual(message.session.session_key, self.session_key)
        self.assertEqual(message.utm_term, "service parts")
        self.assertEqual(message.utm_content, "footer-link")
        self.assertEqual(message.referrer, "https://mail.example.net/campaign")
        self.assertEqual(message.user_agent, "Contact Browser/1.0")
        self.assertEqual(message.status, ContactMessage.Status.NEW)
        self.assertEqual(message.priority, ContactMessage.Priority.NORMAL)
        conversion_event = AnalyticsEvent.objects.get(
            event_name="submit_contact",
        )
        self.assertTrue(conversion_event.metadata["server_side"])
        self.assertEqual(conversion_event.visit_session, message.session)

    @override_settings(
        BEHIND_PROXY=True,
        GEOIP_COUNTRY_HEADER="HTTP_X_GEO_COUNTRY",
        GEOIP_CITY_HEADER="HTTP_X_GEO_CITY",
    )
    def test_geo_headers_and_user_agent_dimensions_are_server_derived(self):
        response = self.client.post(
            "/api/visit-sessions/",
            {
                "session_id": "session-geo123",
                "country": "Spoofed country",
                "city": "Spoofed city",
                "device_type": "spoofed",
                "browser": "spoofed",
                "os": "spoofed",
                "consent_status": "accepted",
            },
            format="json",
            HTTP_X_GEO_COUNTRY="US",
            HTTP_X_GEO_CITY="Austin",
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        )
        self.assertEqual(response.status_code, 201)
        session = VisitSession.objects.get(session_key="session-geo123")
        self.assertEqual(session.country, "US")
        self.assertEqual(session.city, "Austin")
        self.assertEqual(session.device_type, "mobile")
        self.assertEqual(session.browser, "Safari")
        self.assertEqual(session.os, "iOS")


class TurnstileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @override_settings(TURNSTILE_REQUIRED=True, TURNSTILE_SECRET_KEY="test-secret")
    def test_required_forms_fail_closed_without_token(self):
        inquiry_response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Buyer",
                "email": "buyer@example.com",
                "privacy_consent": True,
            },
            format="json",
        )
        contact_response = self.client.post(
            "/api/contact/",
            {
                "name": "Buyer",
                "email": "contact@example.com",
                "message": "Please contact me.",
                "privacy_consent": True,
            },
            format="json",
        )
        self.assertEqual(inquiry_response.status_code, 400)
        self.assertIn("turnstile_token", inquiry_response.data)
        self.assertEqual(contact_response.status_code, 400)
        self.assertIn("turnstile_token", contact_response.data)
        self.assertEqual(Inquiry.objects.count(), 0)
        self.assertEqual(ContactMessage.objects.count(), 0)

    @override_settings(TURNSTILE_REQUIRED=True, TURNSTILE_SECRET_KEY="test-secret")
    @patch("catalog.views.send_contact_notification.delay")
    @patch("catalog.views.send_inquiry_notification.delay")
    @patch("catalog.serializers.verify_turnstile_token")
    def test_forms_verify_exact_expected_actions(
        self,
        mocked_verify,
        mocked_inquiry_task,
        mocked_contact_task,
    ):
        inquiry_response = self.client.post(
            "/api/inquiries/",
            {
                "name": "Buyer",
                "email": "rfq-turnstile@example.com",
                "privacy_consent": True,
                "turnstile_token": "rfq-token",
            },
            format="json",
        )
        contact_response = self.client.post(
            "/api/contact/",
            {
                "name": "Buyer",
                "email": "contact-turnstile@example.com",
                "message": "Please contact me.",
                "privacy_consent": True,
                "turnstile_token": "contact-token",
            },
            format="json",
        )
        self.assertEqual(inquiry_response.status_code, 201)
        self.assertEqual(contact_response.status_code, 201)
        self.assertEqual(
            [item.kwargs["expected_action"] for item in mocked_verify.call_args_list],
            ["rfq", "contact"],
        )
        self.assertEqual(
            [item.args[0] for item in mocked_verify.call_args_list],
            ["rfq-token", "contact-token"],
        )
        self.assertNotIn("turnstile_token", inquiry_response.data)
        self.assertNotIn("turnstile_token", contact_response.data)

    @override_settings(
        TURNSTILE_REQUIRED=True,
        TURNSTILE_SECRET_KEY="test-secret",
        TURNSTILE_TIMEOUT=3,
    )
    def test_siteverify_sends_trusted_ip_and_rejects_action_mismatch(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, size):
                return json.dumps(self.payload).encode("utf-8")

        request = RequestFactory().post(
            "/api/inquiries/",
            REMOTE_ADDR="198.51.100.40",
            HTTP_X_FORWARDED_FOR="203.0.113.250",
        )
        with patch(
            "catalog.turnstile.urlopen",
            return_value=FakeResponse({"success": True, "action": "rfq"}),
        ) as mocked_urlopen:
            verify_turnstile_token("verified-token", request, "rfq")
        verification_request = mocked_urlopen.call_args.args[0]
        payload = parse_qs(verification_request.data.decode("utf-8"))
        self.assertEqual(
            verification_request.full_url,
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        )
        self.assertEqual(payload["response"], ["verified-token"])
        self.assertEqual(payload["remoteip"], ["198.51.100.40"])

        with patch("catalog.turnstile.logger.warning"):
            with patch(
                "catalog.turnstile.urlopen",
                return_value=FakeResponse({"success": True, "action": "contact"}),
            ):
                with self.assertRaises(TurnstileVerificationError):
                    verify_turnstile_token("wrong-action-token", request, "rfq")


class ContentManagementApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        category = Category.objects.create(
            name="Managed products",
            slug="managed-products",
        )
        self.product = Product.objects.create(
            category=category,
            title="Managed axle",
            slug="managed-axle",
            model="MANAGED-3500",
            sku="MANAGED-3500",
            short_description="Managed product",
            status=Product.Status.PUBLISHED,
        )

    def test_product_detail_returns_ordered_typed_images_with_alt_text(self):
        ProductImage.objects.create(
            product=self.product,
            external_url="https://cdn.example.com/detail.jpg",
            image_type=ProductImage.ImageType.DETAIL,
            alt_text="Axle spindle and bearing detail",
            sort_order=20,
        )
        ProductImage.objects.create(
            product=self.product,
            external_url="https://cdn.example.com/product.jpg",
            image_type=ProductImage.ImageType.PRODUCT,
            alt_text="Managed axle product view",
            sort_order=10,
        )
        response = self.client.get(f"/api/products/{self.product.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [image["image_type"] for image in response.data["images"]],
            ["product", "detail"],
        )
        self.assertEqual(
            response.data["images"][0]["alt_text"],
            "Managed axle product view",
        )
        self.assertEqual(
            response.data["images"][0]["image_url"],
            "https://cdn.example.com/product.jpg",
        )

    def test_product_cards_fall_back_to_first_ordered_gallery_upload(self):
        self.product.featured = True
        self.product.save(update_fields=["featured"])
        with isolated_product_image_storage():
            ProductImage.objects.create(
                product=self.product,
                image=SimpleUploadedFile(
                    "gallery.webp",
                    b"RIFF\x00\x00\x00\x00WEBPVP8 ",
                    content_type="image/webp",
                ),
                image_type=ProductImage.ImageType.PRODUCT,
                alt_text="Managed axle gallery view",
                sort_order=5,
            )

            listing = self.client.get("/api/products/")
            self.assertEqual(listing.status_code, 200)
            card_url = listing.data["results"][0]["image_url"]
            self.assertTrue(card_url.startswith("http://testserver/media/"))
            self.assertTrue(card_url.endswith("gallery.webp"))

            home = self.client.get("/api/home/")
            self.assertEqual(home.status_code, 200)
            self.assertEqual(
                home.data["featured_products"][0]["image_url"],
                card_url,
            )

    def test_news_api_only_exposes_currently_published_articles(self):
        published = NewsArticle.objects.create(
            title="How to specify a trailer axle",
            slug="specify-trailer-axle",
            summary="A practical engineering checklist.",
            category="Selection guides",
            tags=["axles", "engineering"],
            reading_minutes=6,
            body=[
                {"type": "heading", "text": "Start with the application"},
                {"type": "paragraph", "text": "Confirm duty cycle and load."},
            ],
            status=NewsArticle.Status.PUBLISHED,
            seo_title="Trailer axle specification guide",
            seo_description="Engineering inputs for an axle RFQ.",
        )
        draft = NewsArticle.objects.create(
            title="Draft article",
            slug="draft-article",
            summary="Not ready.",
            body=[],
            status=NewsArticle.Status.DRAFT,
        )
        NewsArticle.objects.create(
            title="Scheduled article",
            slug="scheduled-article",
            summary="Available later.",
            body=[],
            status=NewsArticle.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
        )

        listing = self.client.get("/api/news/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["slug"], published.slug)
        detail = self.client.get(f"/api/news/{published.slug}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["body"][0]["type"], "heading")
        self.assertEqual(detail.data["category"], "Selection guides")
        self.assertEqual(detail.data["tags"], ["axles", "engineering"])
        self.assertEqual(detail.data["reading_minutes"], 6)
        self.assertEqual(
            self.client.get(f"/api/news/{draft.slug}/").status_code,
            404,
        )

    def test_banner_schedule_and_site_setting_public_projection(self):
        Banner.objects.create(
            title="Current campaign",
            desktop_image_url="https://cdn.example.com/current-desktop.jpg",
            mobile_image_url="https://cdn.example.com/current-mobile.jpg",
            button_text="Request a quote",
            button_link="/rfq",
            sort_order=2,
            is_active=True,
        )
        Banner.objects.create(
            title="Expired campaign",
            ends_at=timezone.now() - timedelta(minutes=1),
            is_active=True,
        )
        Banner.objects.create(
            title="Disabled campaign",
            is_active=False,
        )
        settings_record = SiteSetting.objects.create(
            site_name="AxleForge",
            contact_email="contact@example.com",
            sales_email="private-sales@example.com",
            phone_number="+1 555 0100",
            whatsapp_link="https://wa.me/15550100",
            global_seo_title="Trailer axle components",
            footer_text="Engineering-led trailer components.",
        )

        banners = self.client.get("/api/banners/")
        self.assertEqual(banners.status_code, 200)
        self.assertEqual([item["title"] for item in banners.data], ["Current campaign"])
        site_settings = self.client.get("/api/site-settings/")
        self.assertEqual(site_settings.status_code, 200)
        self.assertEqual(site_settings.data["site_name"], settings_record.site_name)
        self.assertEqual(site_settings.data["contact_email"], "contact@example.com")
        self.assertNotIn("sales_email", site_settings.data)

    def test_cms_models_are_registered_and_v1_alias_is_available(self):
        for model in (NewsArticle, Banner, SiteSetting):
            with self.subTest(model=model.__name__):
                self.assertTrue(django_admin.site.is_registered(model))
        health = self.client.get("/api/v1/health/")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.data["status"], "ok")


class AttachmentSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_attachment_uploads_use_an_independent_stricter_ip_bucket(self):
        request_factory = RequestFactory()

        def upload_request():
            return request_factory.post(
                "/api/inquiries/",
                {
                    "attachments": SimpleUploadedFile(
                        "drawing.pdf",
                        b"%PDF-1.4\n%%EOF\n",
                        content_type="application/pdf",
                    )
                },
                REMOTE_ADDR="198.51.100.77",
            )

        plain_request = request_factory.post(
            "/api/inquiries/",
            {"email": "buyer@example.com"},
            REMOTE_ADDR="198.51.100.77",
        )
        with patch.object(
            AttachmentUploadIpThrottle,
            "rate",
            "1/hour",
            create=True,
        ):
            self.assertTrue(
                AttachmentUploadIpThrottle().allow_request(upload_request(), None)
            )
            self.assertTrue(
                AttachmentUploadIpThrottle().allow_request(plain_request, None)
            )
            self.assertFalse(
                AttachmentUploadIpThrottle().allow_request(upload_request(), None)
            )
        self.assertNotEqual(
            AttachmentUploadIpThrottle.scope,
            RFQIpThrottle.scope,
        )

    @override_settings(
        DJANGO_ENV="development",
        ATTACHMENT_SCANNER_COMMAND="",
        ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT=True,
    )
    @patch(
        "catalog.tasks.scan_inquiry_attachment_task.delay",
        side_effect=_eager_scan_attachment,
    )
    @patch("catalog.views.send_inquiry_notification.delay")
    def test_valid_file_is_randomly_named_scanned_and_deleted_with_inquiry(self, mocked_task, mocked_scan):
        with isolated_attachment_storage():
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/inquiries/",
                    {
                        "name": "Technical Buyer",
                        "email": "drawing@example.com",
                        "privacy_consent": "true",
                        "attachments": SimpleUploadedFile(
                            "drawing.pdf",
                            b"%PDF-1.4\n%%EOF\n",
                            content_type="application/pdf",
                        ),
                    },
                    format="multipart",
                )
            self.assertEqual(response.status_code, 201)
            inquiry = Inquiry.objects.get(reference=response.data["reference"])
            attachment = inquiry.attachments.get()
            stored_path = Path(attachment.file.path)
            self.assertEqual(attachment.scan_status, InquiryAttachment.ScanStatus.CLEAN)
            self.assertTrue(attachment.scanned_at)
            self.assertTrue(attachment.file.name.startswith("quarantine/rfq/"))
            self.assertNotEqual(stored_path.name, "drawing.pdf")
            self.assertRegex(stored_path.name, r"^[0-9a-f]{32}\.pdf$")
            self.assertTrue(stored_path.exists())

            with self.captureOnCommitCallbacks(execute=True):
                inquiry.delete()
            self.assertFalse(stored_path.exists())
            mocked_task.assert_called_once()

    @override_settings(
        DJANGO_ENV="production",
        ATTACHMENT_SCANNER_COMMAND="",
        ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT=False,
    )
    @patch(
        "catalog.tasks.scan_inquiry_attachment_task.delay",
        side_effect=_eager_scan_attachment,
    )
    @patch("catalog.views.send_inquiry_notification.delay")
    def test_production_without_scanner_remains_fail_closed(self, mocked_task, mocked_scan):
        with isolated_attachment_storage():
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/api/inquiries/",
                    {
                        "name": "Technical Buyer",
                        "email": "production-scan@example.com",
                        "privacy_consent": "true",
                        "attachments": SimpleUploadedFile(
                            "drawing.pdf",
                            b"%PDF-1.7\n%%EOF\n",
                            content_type="application/pdf",
                        ),
                    },
                    format="multipart",
                )
            self.assertEqual(response.status_code, 201)
            attachment = InquiryAttachment.objects.get()
            self.assertEqual(attachment.scan_status, InquiryAttachment.ScanStatus.ERROR)
            self.assertIn("No malware scanner", attachment.scan_detail)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class NotificationTaskTests(TestCase):
    def test_inquiry_notifications_are_complete_and_idempotent(self):
        inquiry = Inquiry.objects.create(
            name="Technical Buyer",
            email="buyer@example.com",
            company="Example Manufacturing",
            privacy_consent=True,
            specifications={
                "trailer-axles": {"rated_capacity": {"label": "Rated capacity", "value": "7,000 lb"}},
                "general": {"oem_requirement": "yes"},
            },
            message="Please review the drawing.",
        )
        Category.objects.create(name="Trailer Axles", slug="trailer-axles")
        send_inquiry_notification.run(inquiry.pk)
        inquiry.refresh_from_db()
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(inquiry.reference, mail.outbox[0].subject)
        self.assertIn("Trailer Axles:\n  - Rated capacity: 7,000 lb", mail.outbox[0].body)
        self.assertIn("- OEM requirement: yes", mail.outbox[0].body)
        self.assertIsNotNone(inquiry.notification_sent_at)
        self.assertIsNotNone(inquiry.confirmation_sent_at)
        self.assertIsNone(inquiry.notification_claimed_at)
        self.assertIsNone(inquiry.confirmation_claimed_at)

        send_inquiry_notification.run(inquiry.pk)
        self.assertEqual(len(mail.outbox), 2)

    def test_successful_retry_clears_previous_delivery_error(self):
        inquiry = Inquiry.objects.create(
            name="Technical Buyer",
            email="buyer@example.com",
            privacy_consent=True,
        )
        with patch(
            "catalog.tasks.send_mail",
            side_effect=[1, RuntimeError("smtp unavailable")],
        ):
            with self.assertRaises(Exception):
                send_inquiry_notification.run(inquiry.pk)
        inquiry.refresh_from_db()
        self.assertIsNotNone(inquiry.notification_sent_at)
        self.assertIsNone(inquiry.confirmation_sent_at)
        self.assertIsNone(inquiry.notification_claimed_at)
        self.assertIsNone(inquiry.confirmation_claimed_at)
        self.assertEqual(inquiry.notification_error, "smtp unavailable")

        with patch("catalog.tasks.send_mail", return_value=1):
            send_inquiry_notification.run(inquiry.pk)
        inquiry.refresh_from_db()
        self.assertIsNotNone(inquiry.confirmation_sent_at)
        self.assertIsNone(inquiry.confirmation_claimed_at)
        self.assertEqual(inquiry.notification_error, "")

    def test_contact_emails_include_message_reference(self):
        SiteSetting.objects.create(
            site_name="AxleForge",
            sales_email="managed-sales@example.com",
        )
        message = ContactMessage.objects.create(
            name="Service Buyer",
            email="service@example.com",
            company="Service Fleet",
            message="Please contact our maintenance lead.",
            privacy_consent=True,
        )
        send_contact_notification.run(message.pk)
        message.refresh_from_db()
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(message.reference, mail.outbox[0].subject)
        self.assertIn(message.reference, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["managed-sales@example.com"])
        self.assertIn(message.reference, mail.outbox[1].subject)
        self.assertIn(message.reference, mail.outbox[1].body)

        send_contact_notification.run(message.pk)
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(NOTIFICATION_CLAIM_LEASE_SECONDS=600)
    def test_delivery_claim_is_atomic_reclaimable_and_owner_scoped(self):
        inquiry = Inquiry.objects.create(
            name="Lease Buyer",
            email="lease@example.com",
            privacy_consent=True,
        )
        base_time = timezone.now()
        with patch("catalog.tasks.timezone.now", return_value=base_time):
            first_claim = _claim_delivery(
                Inquiry,
                inquiry.pk,
                sent_field="notification_sent_at",
                claim_field="notification_claimed_at",
            )
        self.assertIsNotNone(first_claim)
        with patch(
            "catalog.tasks.timezone.now",
            return_value=base_time + timedelta(seconds=1),
        ):
            self.assertIsNone(
                _claim_delivery(
                    Inquiry,
                    inquiry.pk,
                    sent_field="notification_sent_at",
                    claim_field="notification_claimed_at",
                )
            )

        Inquiry.objects.filter(pk=inquiry.pk).update(
            notification_claimed_at=base_time - timedelta(seconds=601)
        )
        with patch(
            "catalog.tasks.timezone.now",
            return_value=base_time + timedelta(seconds=2),
        ):
            replacement_claim = _claim_delivery(
                Inquiry,
                inquiry.pk,
                sent_field="notification_sent_at",
                claim_field="notification_claimed_at",
            )
        self.assertIsNotNone(replacement_claim)
        self.assertNotEqual(first_claim, replacement_claim)
        self.assertFalse(
            _release_delivery(
                Inquiry,
                inquiry.pk,
                claim_field="notification_claimed_at",
                claimed_at=first_claim,
            )
        )
        self.assertTrue(
            _release_delivery(
                Inquiry,
                inquiry.pk,
                claim_field="notification_claimed_at",
                claimed_at=replacement_claim,
            )
        )
        inquiry.refresh_from_db()
        self.assertIsNone(inquiry.notification_claimed_at)

    def test_reentrant_duplicate_delivery_cannot_send_duplicate_emails(self):
        inquiry = Inquiry.objects.create(
            name="Concurrent Buyer",
            email="concurrent@example.com",
            privacy_consent=True,
        )
        subjects = []

        def simulated_smtp(subject, *args, **kwargs):
            subjects.append(subject)
            if len(subjects) == 1:
                send_inquiry_notification.run(inquiry.pk)
            return 1

        with patch("catalog.tasks.send_mail", side_effect=simulated_smtp):
            send_inquiry_notification.run(inquiry.pk)

        inquiry.refresh_from_db()
        self.assertEqual(len(subjects), 2)
        self.assertEqual(len({subject.split(" — ")[0] for subject in subjects}), 2)
        self.assertIsNotNone(inquiry.notification_sent_at)
        self.assertIsNotNone(inquiry.confirmation_sent_at)
        self.assertIsNone(inquiry.notification_claimed_at)
        self.assertIsNone(inquiry.confirmation_claimed_at)

    def test_failed_sales_delivery_releases_claim_for_retry(self):
        message = ContactMessage.objects.create(
            name="Retry Contact",
            email="retry-contact@example.com",
            message="Please retry safely.",
            privacy_consent=True,
        )
        with patch("catalog.tasks.send_mail", side_effect=RuntimeError("smtp down")):
            with self.assertRaises(Exception):
                send_contact_notification.run(message.pk)

        message.refresh_from_db()
        self.assertIsNone(message.notification_sent_at)
        self.assertIsNone(message.notification_claimed_at)
        self.assertEqual(message.notification_error, "smtp down")

        with patch("catalog.tasks.send_mail", return_value=1) as mocked_send:
            send_contact_notification.run(message.pk)
        message.refresh_from_db()
        self.assertEqual(mocked_send.call_count, 2)
        self.assertIsNotNone(message.notification_sent_at)
        self.assertIsNotNone(message.confirmation_sent_at)
        self.assertIsNone(message.notification_claimed_at)
        self.assertIsNone(message.confirmation_claimed_at)
        self.assertEqual(message.notification_error, "")

    def test_inquiry_sales_email_contains_permission_controlled_attachment_links(self):
        inquiry = Inquiry.objects.create(
            name="Drawing Buyer",
            email="drawing@example.com",
            privacy_consent=True,
        )
        attachment = InquiryAttachment.objects.create(
            inquiry=inquiry,
            file="quarantine/rfq/example/drawing.pdf",
            original_name="drawing.pdf",
            size=128,
            declared_mime_type="application/pdf",
            scan_status=InquiryAttachment.ScanStatus.CLEAN,
        )

        send_inquiry_notification.run(inquiry.pk)

        expected_link = (
            "http://localhost:8000/admin/inquiry-attachments/"
            f"{attachment.pk}/download/"
        )
        self.assertIn("Attachments (1):", mail.outbox[0].body)
        self.assertIn("drawing.pdf (Clean)", mail.outbox[0].body)
        self.assertIn(expected_link, mail.outbox[0].body)

    def test_admin_email_templates_apply_to_both_forms_and_both_recipients(self):
        SiteSetting.objects.create(
            site_name="AxleForge",
            inquiry_sales_subject_template="Sales {reference} for {company}",
            inquiry_sales_intro_template="Review {reference} for {site_name}.",
            inquiry_confirmation_subject_template="RFQ {reference} received",
            inquiry_confirmation_intro_template="Thanks {name}; {reference} is queued.",
            contact_sales_subject_template="Contact {reference} from {company}",
            contact_sales_intro_template="Handle {reference} for {site_name}.",
            contact_confirmation_subject_template="Message {reference} received",
            contact_confirmation_intro_template="Thanks {name}; we logged {reference}.",
        )
        inquiry = Inquiry.objects.create(
            name="Template Buyer",
            email="template-buyer@example.com",
            company="Template Works",
            privacy_consent=True,
        )
        message = ContactMessage.objects.create(
            name="Template Contact",
            email="template-contact@example.com",
            company="Template Fleet",
            message="Template check",
            privacy_consent=True,
        )

        send_inquiry_notification.run(inquiry.pk)
        send_contact_notification.run(message.pk)

        self.assertEqual(
            mail.outbox[0].subject,
            f"Sales {inquiry.reference} for Template Works",
        )
        self.assertIn(f"Review {inquiry.reference} for AxleForge.", mail.outbox[0].body)
        self.assertEqual(mail.outbox[1].subject, f"RFQ {inquiry.reference} received")
        self.assertIn(f"Thanks Template Buyer; {inquiry.reference} is queued.", mail.outbox[1].body)
        self.assertEqual(
            mail.outbox[2].subject,
            f"Contact {message.reference} from Template Fleet",
        )
        self.assertIn(f"Handle {message.reference} for AxleForge.", mail.outbox[2].body)
        self.assertEqual(
            mail.outbox[3].subject,
            f"Message {message.reference} received",
        )
        self.assertIn(
            f"Thanks Template Contact; we logged {message.reference}.",
            mail.outbox[3].body,
        )

    def test_invalid_admin_email_template_falls_back_safely(self):
        SiteSetting.objects.create(
            site_name="AxleForge",
            contact_sales_subject_template="Unsafe {unknown}",
            contact_sales_intro_template="Malformed {reference",
        )
        message = ContactMessage.objects.create(
            name="Fallback Contact",
            email="fallback@example.com",
            message="Fallback check",
            privacy_consent=True,
        )

        send_contact_notification.run(message.pk)

        self.assertTrue(mail.outbox[0].subject.startswith("Website contact "))
        self.assertIn(message.reference, mail.outbox[0].subject)
        self.assertIn(
            "A new website contact message has been submitted.",
            mail.outbox[0].body,
        )

    def test_user_text_cannot_break_notification_subject_headers(self):
        inquiry = Inquiry.objects.create(
            name="Buyer\r\nBcc: injected@example.com",
            email="buyer@example.com",
            company="Factory\nOperations",
            privacy_consent=True,
        )
        message = ContactMessage.objects.create(
            name="Service\r\nTeam",
            email="service@example.com",
            company="Fleet\nMaintenance",
            message="Please contact us.",
            privacy_consent=True,
        )

        send_inquiry_notification.run(inquiry.pk)
        send_contact_notification.run(message.pk)

        self.assertEqual(len(mail.outbox), 4)
        for email_message in mail.outbox:
            self.assertNotIn("\r", email_message.subject)
            self.assertNotIn("\n", email_message.subject)
        self.assertIn("Factory Operations", mail.outbox[0].subject)
        self.assertIn("Fleet Maintenance", mail.outbox[2].subject)

    def test_replay_command_requeues_persisted_broker_failure(self):
        inquiry = Inquiry.objects.create(
            name="Technical Buyer",
            email="buyer@example.com",
            privacy_consent=True,
            notification_error=f"{QUEUE_ERROR_PREFIX} broker unavailable",
        )
        with patch("catalog.tasks.send_inquiry_notification.delay") as mocked_task:
            call_command("replay_notifications", kind="inquiry")
        inquiry.refresh_from_db()
        mocked_task.assert_called_once_with(inquiry.pk)
        self.assertIsNotNone(inquiry.notification_queued_at)
        self.assertEqual(inquiry.notification_error, "")


class ManagementCommandSafetyTests(TestCase):
    @override_settings(DJANGO_ENV="production")
    def test_seed_demo_is_blocked_in_production(self):
        with self.assertRaisesMessage(
            CommandError,
            "seed_demo is disabled in production environments.",
        ):
            call_command("seed_demo")
        self.assertFalse(Product.objects.exists())


class ProductionSettingsStartupTests(SimpleTestCase):
    def production_env(
        self,
        public_base_url: str,
        **overrides: str,
    ) -> dict[str, str]:
        environment = os.environ.copy()
        for variable_name in (
            "DJANGO_ENV",
            "DJANGO_BEHIND_PROXY",
            "DJANGO_NUM_PROXIES",
            "DJANGO_GEOIP_COUNTRY_HEADER",
            "DJANGO_GEOIP_CITY_HEADER",
        ):
            environment.pop(variable_name, None)
        environment.update(
            {
                "DJANGO_ENV": "production",
                "DJANGO_BEHIND_PROXY": "False",
                "DJANGO_NUM_PROXIES": "0",
                "DJANGO_DEBUG": "False",
                "DJANGO_SECRET_KEY": (
                    "ProductionTestOnly_9vM2xP7rT4zN8kW5cH1fD6yU3sA0bJ7gE2qL9mX4"
                ),
                "DJANGO_ALLOWED_HOSTS": "api.example.com",
                "DJANGO_CORS_ORIGINS": "https://www.example.com",
                "DJANGO_SECURE_SSL_REDIRECT": "True",
                "DJANGO_PUBLIC_MEDIA_URL": "https://cdn.example.com/media/",
                "DJANGO_PUBLIC_BASE_URL": public_base_url,
                "TURNSTILE_SECRET_KEY": "production-settings-test-secret",
                "DEFAULT_FROM_EMAIL": "website@example.com",
                "SALES_NOTIFICATION_EMAIL": "sales@example.com",
                "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                "EMAIL_HOST": "smtp.example.com",
                "EMAIL_PORT": "587",
                "EMAIL_USE_TLS": "True",
                "EMAIL_USE_SSL": "False",
                "EMAIL_TIMEOUT": "15",
                "DATABASE_URL": "sqlite:///:memory:",
                "DJANGO_CACHE_URL": "redis://127.0.0.1:6379/1",
            }
        )
        environment.update(overrides)
        return environment

    def run_deploy_check(
        self,
        public_base_url: str = "https://api.example.com",
        **overrides: str,
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy"],
            cwd=Path(__file__).resolve().parents[1],
            env=self.production_env(public_base_url, **overrides),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_production_rejects_non_public_or_credentialed_base_urls(self):
        for invalid_url in (
            "http://localhost:8000",
            "https://127.0.0.1",
            "https://user:secret@example.com",
            "https://127.1",
            "https://127.0.1",
            "https://0177.0.0.1",
            "https://0x7f.0.0.1",
            "https://127.000.000.001",
        ):
            with self.subTest(url=invalid_url):
                result = self.run_deploy_check(invalid_url)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "DJANGO_PUBLIC_BASE_URL must be a credential-free public HTTPS URL",
                    result.stdout + result.stderr,
                )

    def test_production_accepts_public_https_base_url(self):
        result = self.run_deploy_check("https://api.example.com")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_environment_mode_is_normalized_and_unknown_values_are_rejected(self):
        normalized = self.run_deploy_check(DJANGO_ENV=" Production ")
        self.assertEqual(
            normalized.returncode,
            0,
            normalized.stdout + normalized.stderr,
        )

        for invalid_environment in ("prod", "prodution", "staging", ""):
            with self.subTest(DJANGO_ENV=invalid_environment):
                result = self.run_deploy_check(
                    DJANGO_ENV=invalid_environment,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "DJANGO_ENV must be development, test or production.",
                    result.stdout + result.stderr,
                )

    def test_proxy_count_rejects_invalid_and_inconsistent_values(self):
        cases = (
            (
                {
                    "DJANGO_BEHIND_PROXY": "False",
                    "DJANGO_NUM_PROXIES": "-1",
                },
                "DJANGO_NUM_PROXIES must be a non-negative integer.",
            ),
            (
                {
                    "DJANGO_BEHIND_PROXY": "False",
                    "DJANGO_NUM_PROXIES": "1.5",
                },
                "DJANGO_NUM_PROXIES must be a non-negative integer.",
            ),
            (
                {
                    "DJANGO_BEHIND_PROXY": "False",
                    "DJANGO_NUM_PROXIES": "1",
                },
                "DJANGO_NUM_PROXIES must be 0 when "
                "DJANGO_BEHIND_PROXY=False.",
            ),
            (
                {
                    "DJANGO_BEHIND_PROXY": "True",
                    "DJANGO_NUM_PROXIES": "0",
                },
                "DJANGO_NUM_PROXIES must be greater than 0 when "
                "DJANGO_BEHIND_PROXY=True.",
            ),
        )
        for environment_overrides, expected_error in cases:
            with self.subTest(environment_overrides=environment_overrides):
                result = self.run_deploy_check(**environment_overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    expected_error,
                    result.stdout + result.stderr,
                )

    def test_proxy_count_accepts_consistent_direct_and_proxied_modes(self):
        for environment_overrides in (
            {
                "DJANGO_BEHIND_PROXY": "False",
                "DJANGO_NUM_PROXIES": "0",
            },
            {
                "DJANGO_BEHIND_PROXY": "True",
                "DJANGO_NUM_PROXIES": "1",
            },
        ):
            with self.subTest(environment_overrides=environment_overrides):
                result = self.run_deploy_check(**environment_overrides)
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )

    def test_celery_fail_fast_settings_reject_non_positive_values(self):
        cases = (
            (
                {"CELERY_REDIS_SOCKET_CONNECT_TIMEOUT": "0"},
                "CELERY_REDIS_SOCKET_CONNECT_TIMEOUT must be a positive finite number.",
            ),
            (
                {"CELERY_REDIS_SOCKET_TIMEOUT": "nan"},
                "CELERY_REDIS_SOCKET_TIMEOUT must be a positive finite number.",
            ),
            (
                {"CELERY_TASK_PUBLISH_MAX_RETRIES": "0"},
                "CELERY_TASK_PUBLISH_MAX_RETRIES must be a positive integer.",
            ),
            (
                {"CELERY_TASK_PUBLISH_RETRY_INTERVAL": "-1"},
                "CELERY_TASK_PUBLISH_RETRY_INTERVAL must be a positive finite number.",
            ),
        )
        for environment_overrides, expected_error in cases:
            with self.subTest(environment_overrides=environment_overrides):
                result = self.run_deploy_check(**environment_overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    expected_error,
                    result.stdout + result.stderr,
                )

        valid = self.run_deploy_check(
            CELERY_REDIS_SOCKET_CONNECT_TIMEOUT="0.5",
            CELERY_REDIS_SOCKET_TIMEOUT="0.75",
            CELERY_TASK_PUBLISH_MAX_RETRIES="3",
            CELERY_TASK_PUBLISH_RETRY_INTERVAL="0.1",
        )
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

    def test_email_transport_rejects_ambiguous_or_invalid_values(self):
        cases = (
            (
                {"EMAIL_USE_TLS": "True", "EMAIL_USE_SSL": "True"},
                "EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be True.",
            ),
            (
                {"EMAIL_USE_TLS": "tru"},
                "EMAIL_USE_TLS must be True or False.",
            ),
            (
                {"EMAIL_USE_SSL": "yes"},
                "EMAIL_USE_SSL must be True or False.",
            ),
            (
                {"EMAIL_PORT": "0"},
                "EMAIL_PORT must be an integer from 1 to 65535.",
            ),
            (
                {"EMAIL_PORT": "65536"},
                "EMAIL_PORT must be an integer from 1 to 65535.",
            ),
            (
                {"EMAIL_PORT": "58.7"},
                "EMAIL_PORT must be an integer from 1 to 65535.",
            ),
            (
                {"EMAIL_TIMEOUT": "0"},
                "EMAIL_TIMEOUT must be a positive finite number.",
            ),
            (
                {"EMAIL_TIMEOUT": "nan"},
                "EMAIL_TIMEOUT must be a positive finite number.",
            ),
        )
        for environment_overrides, expected_error in cases:
            with self.subTest(environment_overrides=environment_overrides):
                result = self.run_deploy_check(**environment_overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    expected_error,
                    result.stdout + result.stderr,
                )

        valid_ssl = self.run_deploy_check(
            EMAIL_PORT="465",
            EMAIL_USE_TLS="False",
            EMAIL_USE_SSL="True",
            EMAIL_TIMEOUT="2.5",
        )
        self.assertEqual(
            valid_ssl.returncode,
            0,
            valid_ssl.stdout + valid_ssl.stderr,
        )

    def test_production_rejects_non_smtp_delivery_backends(self):
        for backend in (
            "django.core.mail.backends.console.EmailBackend",
            "django.core.mail.backends.dummy.EmailBackend",
            "django.core.mail.backends.filebased.EmailBackend",
            "django.core.mail.backends.locmem.EmailBackend",
        ):
            with self.subTest(backend=backend):
                result = self.run_deploy_check(EMAIL_BACKEND=backend)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "EMAIL_BACKEND must be "
                    "django.core.mail.backends.smtp.EmailBackend in production.",
                    result.stdout + result.stderr,
                )

    def test_production_rejects_invalid_or_non_public_email_addresses(self):
        cases = (
            {"DEFAULT_FROM_EMAIL": "not-an-email"},
            {"SALES_NOTIFICATION_EMAIL": "also-not-an-email"},
            {"DEFAULT_FROM_EMAIL": "website@localhost"},
            {"SALES_NOTIFICATION_EMAIL": "sales@intranet.local"},
        )
        for environment_overrides in cases:
            with self.subTest(environment_overrides=environment_overrides):
                result = self.run_deploy_check(**environment_overrides)
                self.assertNotEqual(result.returncode, 0)
                setting_name = next(iter(environment_overrides))
                self.assertIn(
                    f"{setting_name} must be a valid email address at a public domain.",
                    result.stdout + result.stderr,
                )

    def test_production_requires_an_smtp_host(self):
        result = self.run_deploy_check(EMAIL_HOST="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "EMAIL_HOST must be configured for SMTP delivery in production.",
            result.stdout + result.stderr,
        )

    def test_production_rejects_unsafe_hosts_origins_and_media_urls(self):
        cases = (
            (
                {"DJANGO_ALLOWED_HOSTS": "*"},
                "DJANGO_ALLOWED_HOSTS must contain only explicit public hostnames",
            ),
            (
                {"DJANGO_ALLOWED_HOSTS": ".example.com"},
                "DJANGO_ALLOWED_HOSTS must contain only explicit public hostnames",
            ),
            (
                {"DJANGO_ALLOWED_HOSTS": "https://api.example.com"},
                "DJANGO_ALLOWED_HOSTS must contain only explicit public hostnames",
            ),
            (
                {"DJANGO_CORS_ORIGINS": "http://www.example.com"},
                "DJANGO_CORS_ORIGINS must be a credential-free public HTTPS URL",
            ),
            (
                {
                    "DJANGO_CORS_ORIGINS":
                        "https://frontend-user:frontend-secret@www.example.com"
                },
                "DJANGO_CORS_ORIGINS must be a credential-free public HTTPS URL",
            ),
            (
                {
                    "DJANGO_PUBLIC_MEDIA_URL":
                        "https://media-user:media-secret@cdn.example.com/media/"
                },
                "DJANGO_PUBLIC_MEDIA_URL must be a credential-free public HTTPS URL",
            ),
            (
                {"DJANGO_PUBLIC_MEDIA_URL": "https://127.0.0.1/media/"},
                "DJANGO_PUBLIC_MEDIA_URL must be a credential-free public HTTPS URL",
            ),
        )
        for environment_overrides, expected_error in cases:
            with self.subTest(environment_overrides=environment_overrides):
                result = self.run_deploy_check(**environment_overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    expected_error,
                    result.stdout + result.stderr,
                )


class CeleryQueueFailFastTests(SimpleTestCase):
    def test_notification_queue_configuration_is_bounded(self):
        from django.conf import settings

        self.assertTrue(send_inquiry_notification.ignore_result)
        self.assertTrue(send_contact_notification.ignore_result)
        self.assertGreater(settings.CELERY_REDIS_SOCKET_CONNECT_TIMEOUT, 0)
        self.assertGreater(settings.CELERY_REDIS_SOCKET_TIMEOUT, 0)
        self.assertFalse(settings.CELERY_REDIS_RETRY_ON_TIMEOUT)
        self.assertEqual(
            settings.CELERY_BROKER_TRANSPORT_OPTIONS,
            {
                "socket_connect_timeout":
                    settings.CELERY_REDIS_SOCKET_CONNECT_TIMEOUT,
                "socket_timeout": settings.CELERY_REDIS_SOCKET_TIMEOUT,
                "retry_on_timeout": False,
            },
        )
        self.assertTrue(settings.CELERY_TASK_PUBLISH_RETRY)
        self.assertGreater(
            settings.CELERY_TASK_PUBLISH_RETRY_POLICY["max_retries"],
            0,
        )
        self.assertFalse(settings.CELERY_RESULT_BACKEND_ALWAYS_RETRY)
        self.assertEqual(
            settings.CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS["retry_policy"],
            settings.CELERY_TASK_PUBLISH_RETRY_POLICY,
        )

    def test_unreachable_redis_returns_quickly_and_persists_queue_error(self):
        backend_directory = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            database_path = (Path(directory) / "queue-probe.sqlite3").as_posix()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved_socket:
                reserved_socket.bind(("127.0.0.1", 0))
                unavailable_port = reserved_socket.getsockname()[1]
                environment = os.environ.copy()
                environment.update(
                    {
                        "DATABASE_URL": f"sqlite:///{database_path}",
                        "DJANGO_ENV": "test",
                        "DJANGO_DEBUG": "True",
                        "DJANGO_BEHIND_PROXY": "False",
                        "DJANGO_NUM_PROXIES": "0",
                        "DJANGO_CACHE_URL": "",
                        "REDIS_URL":
                            f"redis://127.0.0.1:{unavailable_port}/15",
                        "CELERY_REDIS_SOCKET_CONNECT_TIMEOUT": "0.5",
                        "CELERY_REDIS_SOCKET_TIMEOUT": "0.5",
                        "CELERY_TASK_PUBLISH_MAX_RETRIES": "2",
                        "CELERY_TASK_PUBLISH_RETRY_INTERVAL": "0.1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    }
                )
                migrated = subprocess.run(
                    [
                        sys.executable,
                        "manage.py",
                        "migrate",
                        "--noinput",
                        "--verbosity",
                        "0",
                    ],
                    cwd=backend_directory,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    migrated.returncode,
                    0,
                    migrated.stdout + migrated.stderr,
                )

                probe_code = (
                    "import json, logging, time; "
                    "from catalog.models import Inquiry; "
                    "from catalog.tasks import enqueue_inquiry_notification; "
                    "logging.disable(logging.CRITICAL); "
                    "inquiry=Inquiry.objects.create("
                    "name='Queue Probe',email='queue-probe@example.com',"
                    "privacy_consent=True); "
                    "started=time.monotonic(); "
                    "queued=enqueue_inquiry_notification(inquiry.pk); "
                    "elapsed=time.monotonic()-started; "
                    "inquiry.refresh_from_db(); "
                    "print('QUEUE_PROBE='+json.dumps({"
                    "'queued':queued,'elapsed':elapsed,"
                    "'queued_at_is_none':inquiry.notification_queued_at is None,"
                    "'error':inquiry.notification_error"
                    "}))"
                )
                probed = subprocess.run(
                    [sys.executable, "manage.py", "shell", "-c", probe_code],
                    cwd=backend_directory,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )

        self.assertEqual(
            probed.returncode,
            0,
            probed.stdout + probed.stderr,
        )
        marker = "QUEUE_PROBE="
        payload_line = next(
            (
                line
                for line in probed.stdout.splitlines()
                if line.startswith(marker)
            ),
            "",
        )
        self.assertTrue(payload_line, probed.stdout + probed.stderr)
        payload = json.loads(payload_line.removeprefix(marker))
        self.assertFalse(payload["queued"])
        self.assertLess(payload["elapsed"], 5)
        self.assertTrue(payload["queued_at_is_none"])
        self.assertTrue(payload["error"].startswith(QUEUE_ERROR_PREFIX))


class CanonicalSpecMigrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = importlib.import_module(
            "catalog.migrations.0013_canonical_product_specs"
        )

    def test_dimension_parser_normalizes_and_converts_supported_units(self):
        parse = self.migration._parse_dimension
        self.assertEqual(
            parse("95", "in", label="test"),
            Decimal("95.00"),
        )
        self.assertEqual(
            parse('95"', "mm", label="test"),
            Decimal("2413.00"),
        )
        self.assertEqual(
            parse(" 2413 MM ", "in", label="test"),
            Decimal("95.00"),
        )
        self.assertEqual(
            parse("95 Inches", "in", label="test"),
            Decimal("95.00"),
        )
        self.assertIsNone(parse("", "in", label="test"))

    def test_dimension_parser_aborts_on_ambiguous_or_invalid_data(self):
        parse = self.migration._parse_dimension
        for invalid in ("ninety-five", "95 ft", "0", "-1", "NaN"):
            with self.subTest(value=invalid), self.assertRaises(RuntimeError):
                parse(invalid, "in", label="test")

    def test_enum_migration_normalizes_legacy_seed_aliases(self):
        normalize = self.migration._normalize_enum
        self.assertEqual(
            normalize("Electric Drum Brake", self.migration.BRAKE_ALIASES, label="test"),
            "Electric Drum",
        )
        self.assertEqual(
            normalize("Hot-dip galvanised", self.migration.FINISH_ALIASES, label="test"),
            "Hot-Dip Galvanized",
        )
        self.assertEqual(
            normalize("round tube", self.migration.TUBE_SHAPE_ALIASES, label="test"),
            "Round",
        )
        with self.assertRaises(RuntimeError):
            normalize("triangle", self.migration.TUBE_SHAPE_ALIASES, label="test")


class ProtectedDocumentMigrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = importlib.import_module(
            "catalog.migrations.0015_protect_product_document_files"
        )

    @staticmethod
    def fake_apps(file_names):
        documents = [
            type(
                "FakeDocument",
                (),
                {"pk": index, "file": type("FakeFile", (), {"name": file_name})()},
            )()
            for index, file_name in enumerate(file_names, start=1)
        ]

        class FakeObjects:
            def exclude(self, **kwargs):
                return self

            def iterator(self):
                return iter(documents)

        fake_model = type("FakeProductDocument", (), {"objects": FakeObjects()})
        return type(
            "FakeApps",
            (),
            {"get_model": lambda self, app_label, model_name: fake_model},
        )()

    def test_shared_legacy_file_is_relocated_once(self):
        with TemporaryDirectory() as source_directory:
            with TemporaryDirectory() as destination_directory:
                source_root = Path(source_directory)
                destination_root = Path(destination_directory)
                relative_name = "documents/2026/07/shared.pdf"
                source_file = source_root / relative_name
                source_file.parent.mkdir(parents=True)
                source_file.write_bytes(b"%PDF-1.4\n%%EOF\n")
                apps = self.fake_apps([relative_name, relative_name])

                self.migration._relocate_documents(
                    apps,
                    source_root,
                    destination_root,
                )

                self.assertFalse(source_file.exists())
                self.assertEqual(
                    (destination_root / relative_name).read_bytes(),
                    b"%PDF-1.4\n%%EOF\n",
                )

    def test_unsafe_legacy_file_name_is_rejected_before_file_changes(self):
        with TemporaryDirectory() as source_directory:
            with TemporaryDirectory() as destination_directory:
                apps = self.fake_apps(["../../outside.pdf"])
                with self.assertRaisesMessage(
                    RuntimeError,
                    "Unsafe product-document storage name",
                ):
                    self.migration._relocate_documents(
                        apps,
                        Path(source_directory),
                        Path(destination_directory),
                    )


class LegacyInquiryAttachmentCleanupMigrationTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migration = importlib.import_module(
            "catalog.migrations.0016_remove_public_inquiry_attachment_copies"
        )

    @staticmethod
    def fake_apps(file_names):
        attachments = [
            type(
                "FakeAttachment",
                (),
                {"pk": index, "file": type("FakeFile", (), {"name": file_name})()},
            )()
            for index, file_name in enumerate(file_names, start=1)
        ]

        class FakeObjects:
            def exclude(self, **kwargs):
                return self

            def iterator(self):
                return iter(attachments)

        fake_model = type("FakeInquiryAttachment", (), {"objects": FakeObjects()})
        return type(
            "FakeApps",
            (),
            {"get_model": lambda self, app_label, model_name: fake_model},
        )()

    def test_public_only_file_is_moved_to_private_storage(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            relative_name = "quarantine/rfq/2026/07/RFQ-1/drawing.pdf"
            public_file = public_root / relative_name
            public_file.parent.mkdir(parents=True)
            public_file.write_bytes(b"%PDF-1.4\npublic-only\n%%EOF\n")

            self.migration._cleanup_legacy_attachment_copies(
                self.fake_apps([relative_name]),
                public_root,
                private_root,
            )

            self.assertFalse(public_file.exists())
            self.assertEqual(
                (private_root / relative_name).read_bytes(),
                b"%PDF-1.4\npublic-only\n%%EOF\n",
            )

    def test_identical_public_duplicate_is_deleted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            relative_name = "quarantine/rfq/2026/07/RFQ-2/drawing.pdf"
            public_file = public_root / relative_name
            private_file = private_root / relative_name
            public_file.parent.mkdir(parents=True)
            private_file.parent.mkdir(parents=True)
            payload = b"%PDF-1.4\nidentical\n%%EOF\n"
            public_file.write_bytes(payload)
            private_file.write_bytes(payload)

            self.migration._cleanup_legacy_attachment_copies(
                self.fake_apps([relative_name]),
                public_root,
                private_root,
            )

            self.assertFalse(public_file.exists())
            self.assertEqual(private_file.read_bytes(), payload)

    def test_conflict_fails_before_any_file_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            movable_name = "quarantine/rfq/2026/07/RFQ-3/movable.pdf"
            conflict_name = "quarantine/rfq/2026/07/RFQ-3/conflict.pdf"
            movable_public = public_root / movable_name
            conflict_public = public_root / conflict_name
            conflict_private = private_root / conflict_name
            movable_public.parent.mkdir(parents=True)
            conflict_private.parent.mkdir(parents=True)
            movable_public.write_bytes(b"move only after preflight")
            conflict_public.write_bytes(b"public version")
            conflict_private.write_bytes(b"private version")

            with self.assertRaisesMessage(RuntimeError, "conflicting copies"):
                self.migration._cleanup_legacy_attachment_copies(
                    self.fake_apps([movable_name, conflict_name]),
                    public_root,
                    private_root,
                )

            self.assertEqual(movable_public.read_bytes(), b"move only after preflight")
            self.assertFalse((private_root / movable_name).exists())
            self.assertEqual(conflict_public.read_bytes(), b"public version")
            self.assertEqual(conflict_private.read_bytes(), b"private version")

    def test_private_only_file_is_accepted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            relative_name = "quarantine/rfq/2026/07/RFQ-4/private.pdf"
            private_file = private_root / relative_name
            private_file.parent.mkdir(parents=True)
            private_file.write_bytes(b"already private")

            self.migration._cleanup_legacy_attachment_copies(
                self.fake_apps([relative_name]),
                public_root,
                private_root,
            )

            self.assertFalse((public_root / relative_name).exists())
            self.assertEqual(private_file.read_bytes(), b"already private")

    def test_shared_file_path_is_processed_once(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            relative_name = "quarantine/rfq/2026/07/RFQ-5/shared.pdf"
            public_file = public_root / relative_name
            public_file.parent.mkdir(parents=True)
            public_file.write_bytes(b"shared attachment")

            self.migration._cleanup_legacy_attachment_copies(
                self.fake_apps([relative_name, relative_name]),
                public_root,
                private_root,
            )

            self.assertFalse(public_file.exists())
            self.assertEqual(
                (private_root / relative_name).read_bytes(),
                b"shared attachment",
            )

    def test_path_escape_fails_before_any_file_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            movable_name = "quarantine/rfq/2026/07/RFQ-6/movable.pdf"
            movable_public = public_root / movable_name
            movable_public.parent.mkdir(parents=True)
            movable_public.write_bytes(b"must remain public")

            with self.assertRaisesMessage(
                RuntimeError,
                "Unsafe inquiry-attachment storage name",
            ):
                self.migration._cleanup_legacy_attachment_copies(
                    self.fake_apps([movable_name, "../../outside.pdf"]),
                    public_root,
                    private_root,
                )

            self.assertEqual(movable_public.read_bytes(), b"must remain public")
            self.assertFalse((private_root / movable_name).exists())

    def test_missing_file_fails_before_any_file_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            movable_name = "quarantine/rfq/2026/07/RFQ-7/movable.pdf"
            missing_name = "quarantine/rfq/2026/07/RFQ-7/missing.pdf"
            movable_public = public_root / movable_name
            movable_public.parent.mkdir(parents=True)
            movable_public.write_bytes(b"must remain until all files pass")

            with self.assertRaisesMessage(RuntimeError, "missing:"):
                self.migration._cleanup_legacy_attachment_copies(
                    self.fake_apps([movable_name, missing_name]),
                    public_root,
                    private_root,
                )

            self.assertEqual(
                movable_public.read_bytes(),
                b"must remain until all files pass",
            )
            self.assertFalse((private_root / movable_name).exists())

    def test_unreferenced_public_file_is_moved_to_private_storage(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            relative_name = "quarantine/rfq/2026/07/deleted-row/orphan.step"
            orphan_public = public_root / relative_name
            orphan_public.parent.mkdir(parents=True)
            orphan_public.write_bytes(b"orphaned RFQ evidence")

            self.migration._cleanup_legacy_attachment_copies(
                self.fake_apps([]),
                public_root,
                private_root,
            )

            self.assertFalse(orphan_public.exists())
            self.assertEqual(
                (private_root / relative_name).read_bytes(),
                b"orphaned RFQ evidence",
            )

    def test_unreferenced_conflict_fails_before_any_file_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            movable_name = "quarantine/rfq/2026/07/deleted-row/movable.step"
            conflict_name = "quarantine/rfq/2026/07/deleted-row/conflict.step"
            movable_public = public_root / movable_name
            conflict_public = public_root / conflict_name
            conflict_private = private_root / conflict_name
            movable_public.parent.mkdir(parents=True)
            conflict_private.parent.mkdir(parents=True)
            movable_public.write_bytes(b"must remain until orphan preflight passes")
            conflict_public.write_bytes(b"public orphan")
            conflict_private.write_bytes(b"private orphan")

            with self.assertRaisesMessage(RuntimeError, "conflicting copies"):
                self.migration._cleanup_legacy_attachment_copies(
                    self.fake_apps([]),
                    public_root,
                    private_root,
                )

            self.assertEqual(
                movable_public.read_bytes(),
                b"must remain until orphan preflight passes",
            )
            self.assertFalse((private_root / movable_name).exists())
            self.assertEqual(conflict_public.read_bytes(), b"public orphan")
            self.assertEqual(conflict_private.read_bytes(), b"private orphan")

    def test_public_namespace_link_fails_before_any_file_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            public_root = root / "public"
            private_root = root / "private"
            namespace = public_root / "quarantine" / "rfq"
            namespace.mkdir(parents=True)
            movable_public = namespace / "movable.step"
            movable_public.write_bytes(b"must remain beside unsafe link")
            outside_directory = root / "outside"
            outside_directory.mkdir()
            (outside_directory / "secret.step").write_bytes(b"outside")
            linked_directory = namespace / "linked"
            try:
                linked_directory.symlink_to(
                    outside_directory,
                    target_is_directory=True,
                )
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"Directory symlinks are unavailable: {exc}")

            with self.assertRaisesMessage(RuntimeError, "link or reparse point"):
                self.migration._cleanup_legacy_attachment_copies(
                    self.fake_apps([]),
                    public_root,
                    private_root,
                )

            self.assertEqual(
                movable_public.read_bytes(),
                b"must remain beside unsafe link",
            )
            self.assertFalse(
                (private_root / "quarantine" / "rfq" / "movable.step").exists()
            )
            self.assertEqual(
                (outside_directory / "secret.step").read_bytes(),
                b"outside",
            )

    def test_public_storage_root_link_fails_before_any_file_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            actual_public_root = root / "actual-public"
            private_root = root / "private"
            relative_name = "quarantine/rfq/2026/07/root-link/orphan.step"
            orphan_public = actual_public_root / relative_name
            orphan_public.parent.mkdir(parents=True)
            orphan_public.write_bytes(b"must remain behind unsafe root link")
            linked_public_root = root / "public-link"
            try:
                linked_public_root.symlink_to(
                    actual_public_root,
                    target_is_directory=True,
                )
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"Directory symlinks are unavailable: {exc}")

            with self.assertRaisesMessage(
                RuntimeError,
                "storage root is a link or reparse point",
            ):
                self.migration._cleanup_legacy_attachment_copies(
                    self.fake_apps([]),
                    linked_public_root,
                    private_root,
                )

            self.assertEqual(
                orphan_public.read_bytes(),
                b"must remain behind unsafe root link",
            )
            self.assertFalse((private_root / relative_name).exists())


@override_settings(
    ADMIN_LOGIN_MAX_FAILURES=2,
    ADMIN_LOGIN_FAILURE_WINDOW=60,
)
class AdminLoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_superuser(
            username="rate-limit-admin",
            email="rate-limit@example.com",
            password="correct-password",
        )
        self.client = Client()

    def test_failed_login_limit_ignores_untrusted_forwarded_for(self):
        remote_addr = "198.51.100.50"
        for forwarded_for in ("203.0.113.1", "203.0.113.2"):
            response = self.client.post(
                "/admin/login/",
                {
                    "username": self.user.username,
                    "password": "wrong-password",
                },
                REMOTE_ADDR=remote_addr,
                HTTP_X_FORWARDED_FOR=forwarded_for,
            )
            self.assertEqual(response.status_code, 200)

        blocked = self.client.post(
            "/admin/login/",
            {
                "username": self.user.username,
                "password": "wrong-password",
            },
            REMOTE_ADDR=remote_addr,
            HTTP_X_FORWARDED_FOR="203.0.113.3",
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], "60")
        self.assertContains(
            blocked,
            "Too many unsuccessful login attempts.",
            status_code=429,
        )

    def test_successful_login_clears_failed_attempts(self):
        remote_addr = "198.51.100.51"
        failed = self.client.post(
            "/admin/login/",
            {
                "username": self.user.username,
                "password": "wrong-password",
            },
            REMOTE_ADDR=remote_addr,
        )
        self.assertEqual(failed.status_code, 200)
        succeeded = self.client.post(
            "/admin/login/",
            {
                "username": self.user.username,
                "password": "correct-password",
            },
            REMOTE_ADDR=remote_addr,
        )
        self.assertEqual(succeeded.status_code, 302)
        self.client.logout()

        first_after_success = self.client.post(
            "/admin/login/",
            {
                "username": self.user.username,
                "password": "wrong-password",
            },
            REMOTE_ADDR=remote_addr,
        )
        second_after_success = self.client.post(
            "/admin/login/",
            {
                "username": self.user.username,
                "password": "wrong-password",
            },
            REMOTE_ADDR=remote_addr,
        )
        blocked = self.client.post(
            "/admin/login/",
            {
                "username": self.user.username,
                "password": "wrong-password",
            },
            REMOTE_ADDR=remote_addr,
        )
        self.assertEqual(first_after_success.status_code, 200)
        self.assertEqual(second_after_success.status_code, 200)
        self.assertEqual(blocked.status_code, 429)


class AdminInterfaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin-review", email="admin-review@example.com", password="test-password"
        )
        self.client.force_login(self.user)

    def test_dashboard_renders_branded_operations_overview(self):
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/index.html")
        self.assertContains(response, "Catalogue and enquiry control.")
        self.assertContains(response, "catalog/admin/axleforge-admin.css")

    def test_dashboard_does_not_leak_inquiries_to_staff_without_permission(self):
        inquiry = Inquiry.objects.create(
            name="Sensitive Buyer",
            email="private-buyer@example.com",
            company="Sensitive Company",
            privacy_consent=True,
        )
        restricted_user = get_user_model().objects.create_user(
            username="restricted-staff",
            password="test-password",
            is_staff=True,
        )
        restricted_client = Client()
        restricted_client.force_login(restricted_user)

        response = restricted_client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(restricted_user.has_perm("catalog.view_inquiry"))
        self.assertNotContains(response, inquiry.reference)
        self.assertNotContains(response, "Sensitive Buyer")
        self.assertNotContains(response, "Sensitive Company")
        self.assertNotContains(response, "private-buyer@example.com")

    def test_private_download_requires_clean_status_and_writes_admin_log(self):
        inquiry = Inquiry.objects.create(
            name="Technical Buyer",
            email="buyer@example.com",
            privacy_consent=True,
        )
        with isolated_attachment_storage():
            attachment = InquiryAttachment.objects.create(
                inquiry=inquiry,
                file=SimpleUploadedFile(
                    "drawing.pdf",
                    b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                original_name="drawing.pdf",
                size=16,
                declared_mime_type="application/pdf",
            )
            url = f"/admin/inquiry-attachments/{attachment.pk}/download/"

            quarantined = self.client.get(url)
            self.assertEqual(quarantined.status_code, 423)
            self.assertFalse(LogEntry.objects.filter(user=self.user).exists())

            attachment.scan_status = InquiryAttachment.ScanStatus.CLEAN
            attachment.save(update_fields=["scan_status", "updated_at"])
            downloaded = self.client.get(url)
            self.assertEqual(downloaded.status_code, 200)

            log_entry = LogEntry.objects.get(user=self.user)
            self.assertEqual(log_entry.object_id, str(attachment.pk))
            self.assertIn("Downloaded private inquiry attachment", log_entry.change_message)
            close_streaming_file(downloaded)

    def test_bulk_workflow_actions_write_per_object_admin_logs(self):
        inquiry = Inquiry.objects.create(
            name="Qualified Buyer",
            email="qualified@example.com",
            privacy_consent=True,
        )
        contact_message = ContactMessage.objects.create(
            name="Resolved Buyer",
            email="resolved@example.com",
            message="Please close this request.",
            privacy_consent=True,
        )
        request = RequestFactory().post("/admin/catalog/")
        request.user = self.user

        InquiryAdmin(Inquiry, django_admin.site).mark_qualified(
            request,
            Inquiry.objects.filter(pk=inquiry.pk),
        )
        ContactMessageAdmin(ContactMessage, django_admin.site).mark_resolved(
            request,
            ContactMessage.objects.filter(pk=contact_message.pk),
        )

        inquiry.refresh_from_db()
        contact_message.refresh_from_db()
        self.assertEqual(inquiry.status, Inquiry.Status.QUALIFIED)
        self.assertEqual(
            contact_message.status,
            ContactMessage.Status.RESOLVED,
        )
        self.assertTrue(contact_message.is_resolved)
        logs = LogEntry.objects.filter(user=self.user).order_by("id")
        self.assertEqual(logs.count(), 2)
        self.assertIn("marked enquiry as qualified", logs[0].change_message)
        self.assertIn("marked contact message as resolved", logs[1].change_message)

    def test_protected_document_admin_and_product_inline_render_controlled_links(self):
        category = Category.objects.create(
            name="Admin axle documents",
            slug="admin-axle-documents",
        )
        product = Product.objects.create(
            category=category,
            title="Admin document axle",
            slug="admin-document-axle",
            model="ADMIN-DOC-1",
            sku="ADMIN-DOC-1",
            short_description="Admin protected-document rendering test.",
            status=Product.Status.PUBLISHED,
        )
        with isolated_product_document_storage():
            document = ProductDocument.objects.create(
                product=product,
                title="Protected dimensional sheet",
                document_type=ProductDocument.DocumentType.DRAWING,
                file=SimpleUploadedFile(
                    "protected.pdf",
                    b"%PDF-1.4\n%%EOF\n",
                    content_type="application/pdf",
                ),
                is_active=True,
            )
            controlled_path = reverse(
                "document-download",
                kwargs={"pk": document.pk},
            )

            document_response = self.client.get(
                f"/admin/catalog/productdocument/{document.pk}/change/"
            )
            product_response = self.client.get(
                f"/admin/catalog/product/{product.pk}/change/"
            )

            for response in (document_response, product_response):
                with self.subTest(path=response.request["PATH_INFO"]):
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, "Stored privately: protected.pdf")
                    self.assertContains(response, controlled_path)
                    self.assertNotContains(response, "/media/documents/")

    def test_followup_inline_records_actor_and_next_followup(self):
        inquiry = Inquiry.objects.create(
            name="Technical Buyer",
            email="followup@example.com",
            privacy_consent=True,
            admin_notes="Confirm annual volume before quoting.",
        )
        next_followup = timezone.now() + timedelta(days=2)
        followup = InquiryFollowup(
            inquiry=inquiry,
            action_type=InquiryFollowup.ActionType.CALL,
            content="Discussed the drawing revision.",
            next_followup_at=next_followup,
        )

        class FakeFormset:
            model = InquiryFollowup
            deleted_objects = ()

            def save(self, commit):
                return [followup]

            def save_m2m(self):
                return None

        request = RequestFactory().post(
            f"/admin/catalog/inquiry/{inquiry.pk}/change/"
        )
        request.user = self.user
        model_admin = InquiryAdmin(Inquiry, django_admin.site)
        self.assertIn(InquiryFollowupInline, model_admin.inlines)
        model_admin.save_formset(
            request,
            form=None,
            formset=FakeFormset(),
            change=True,
        )

        followup.refresh_from_db()
        inquiry.refresh_from_db()
        self.assertEqual(followup.user, self.user)
        self.assertEqual(inquiry.next_followup_at, next_followup)
        self.assertEqual(
            inquiry.admin_notes,
            "Confirm annual volume before quoting.",
        )


class SecurePublicExternalURLTests(TestCase):
    external_url_fields = (
        (Application, "image_url"),
        (Banner, "desktop_image_url"),
        (Banner, "mobile_image_url"),
        (Category, "image_url"),
        (Product, "image_url"),
        (ProductDocument, "external_url"),
        (ProductImage, "external_url"),
        (NewsArticle, "cover_image_url"),
        (SiteSetting, "default_og_image_url"),
        (SiteSetting, "site_logo_url"),
        (SiteSetting, "whatsapp_link"),
    )

    def test_all_public_external_url_fields_require_credential_free_https(self):
        unsafe_urls = (
            "ftp://cdn.example.com/public/file.pdf",
            "https://user:secret@cdn.example.com/public/file.pdf",
            "https://localhost/public/file.pdf",
            "https://127.0.0.1/public/file.pdf",
            "https://127.1/public/file.pdf",
            "https://0177.0.0.1/public/file.pdf",
            "https://0x7f.0.0.1/public/file.pdf",
            "https://10.0.0.1/public/file.pdf",
            "https://cdn.internal/public/file.pdf",
        )
        for model, field_name in self.external_url_fields:
            field = model._meta.get_field(field_name)
            with self.subTest(model=model.__name__, field=field_name, case="valid"):
                self.assertEqual(
                    field.clean("https://cdn.example.com/public/file.pdf", None),
                    "https://cdn.example.com/public/file.pdf",
                )
            for unsafe_url in unsafe_urls:
                with self.subTest(
                    model=model.__name__,
                    field=field_name,
                    url=unsafe_url,
                ):
                    with self.assertRaises(ValidationError):
                        field.clean(unsafe_url, None)

    def test_exposed_model_serializer_url_fields_reject_unsafe_input(self):
        cases = (
            (
                CategorySerializer,
                {"name": "Axles", "slug": "axles"},
                "image_url",
            ),
            (
                ApplicationSerializer,
                {"name": "Utility", "slug": "utility"},
                "image_url",
            ),
            (
                SiteSettingSerializer,
                {"site_name": "AxleForge"},
                "whatsapp_link",
            ),
        )
        for serializer_class, base_data, field_name in cases:
            for unsafe_url in (
                "ftp://cdn.example.com/file.jpg",
                "https://user:secret@cdn.example.com/file.jpg",
                "https://127.0.0.1/file.jpg",
                "https://cdn.internal/file.jpg",
            ):
                with self.subTest(
                    serializer=serializer_class.__name__,
                    url=unsafe_url,
                ):
                    serializer = serializer_class(
                        data={**base_data, field_name: unsafe_url}
                    )
                    self.assertFalse(serializer.is_valid())
                    self.assertIn(field_name, serializer.errors)

    def test_public_serializers_fail_closed_for_unvalidated_legacy_urls(self):
        credential_url = "https://user:secret@cdn.example.com/private.jpg"
        ftp_url = "ftp://cdn.example.com/public.jpg"
        category = Category.objects.create(
            name="Legacy category",
            slug="legacy-category",
            image_url=ftp_url,
        )
        application = Application.objects.create(
            name="Legacy application",
            slug="legacy-application",
            image_url=credential_url,
        )
        product = Product.objects.create(
            category=category,
            title="Legacy product",
            slug="legacy-product",
            model="LEGACY-1",
            sku="LEGACY-1",
            short_description="Legacy URL safety fixture.",
            image_url=ftp_url,
        )
        product_image = ProductImage.objects.create(
            product=product,
            external_url=credential_url,
            alt_text="Legacy product image",
        )
        article = NewsArticle.objects.create(
            title="Legacy article",
            slug="legacy-article",
            summary="Legacy URL safety fixture.",
            cover_image_url=ftp_url,
        )
        banner = Banner.objects.create(
            title="Legacy banner",
            desktop_image_url=ftp_url,
            mobile_image_url=credential_url,
        )
        site_setting = SiteSetting.objects.create(
            site_name="AxleForge",
            site_logo_url=ftp_url,
            default_og_image_url=credential_url,
            whatsapp_link=credential_url,
        )

        self.assertEqual(CategorySerializer(category).data["image_url"], "")
        self.assertEqual(ApplicationSerializer(application).data["image_url"], "")
        self.assertEqual(ProductListSerializer(product).data["image_url"], "")
        self.assertEqual(ProductImageSerializer(product_image).data["image_url"], "")
        self.assertEqual(
            NewsArticleListSerializer(article).data["cover_image_url"],
            "",
        )
        banner_data = BannerSerializer(banner).data
        self.assertEqual(banner_data["desktop_image_url"], "")
        self.assertEqual(banner_data["mobile_image_url"], "")
        site_data = SiteSettingSerializer(site_setting).data
        self.assertEqual(site_data["site_logo_url"], "")
        self.assertEqual(site_data["default_og_image_url"], "")
        self.assertEqual(site_data["whatsapp_link"], "")


class PublicFormOriginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_cross_site_origin_is_rejected_on_rfq(self):
        response = self.client.post(
            "/api/inquiries/",
            {"name": "A", "email": "a@example.com", "privacy_consent": True},
            format="json",
            HTTP_ORIGIN="https://evil.example",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_cross_site_origin_is_rejected_on_versioned_rfq(self):
        response = self.client.post(
            "/api/v1/inquiries/",
            {"name": "A", "email": "a@example.com", "privacy_consent": True},
            format="json",
            HTTP_ORIGIN="https://evil.example",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_cross_site_origin_is_rejected_on_versioned_contact(self):
        response = self.client.post(
            "/api/v1/contact/",
            {
                "name": "A",
                "email": "a@example.com",
                "message": "Hello",
                "privacy_consent": True,
            },
            format="json",
            HTTP_ORIGIN="https://evil.example",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_trusted_origin_is_accepted_on_rfq(self):
        with patch("catalog.views.send_inquiry_notification.delay"):
            response = self.client.post(
                "/api/inquiries/",
                {
                    "name": "Trusted",
                    "email": "trusted@example.com",
                    "privacy_consent": True,
                },
                format="json",
                HTTP_ORIGIN="https://www.example.com",
            )
        self.assertEqual(response.status_code, 201)

    def test_same_host_origin_is_accepted(self):
        with patch("catalog.views.send_inquiry_notification.delay"):
            response = self.client.post(
                "/api/contact/",
                {
                    "name": "Local",
                    "email": "local@example.com",
                    "subject": "Hello",
                    "message": "Question about capacity.",
                    "privacy_consent": True,
                },
                format="json",
                HTTP_ORIGIN="http://testserver",
            )
        self.assertEqual(response.status_code, 201)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_browser_user_agent_without_origin_is_rejected(self):
        response = self.client.post(
            "/api/inquiries/",
            {"name": "A", "email": "a@example.com", "privacy_consent": True},
            format="json",
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_browser_user_agent_without_origin_is_rejected_on_contact(self):
        response = self.client.post(
            "/api/contact/",
            {
                "name": "A",
                "email": "a@example.com",
                "message": "Hello",
                "privacy_consent": True,
            },
            format="json",
            HTTP_USER_AGENT=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/121.0"
            ),
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_non_browser_client_without_origin_is_allowed(self):
        with patch("catalog.views.send_inquiry_notification.delay"):
            response = self.client.post(
                "/api/inquiries/",
                {"name": "Bot", "email": "bot@example.com", "privacy_consent": True},
                format="json",
                HTTP_USER_AGENT="curl/8.4.0",
            )
        self.assertEqual(response.status_code, 201)

    @override_settings(CSRF_TRUSTED_ORIGINS=["https://www.example.com"])
    def test_missing_user_agent_and_origin_is_allowed_for_api_clients(self):
        with patch("catalog.views.send_inquiry_notification.delay"):
            response = self.client.post(
                "/api/inquiries/",
                {"name": "Api", "email": "api@example.com", "privacy_consent": True},
                format="json",
            )
        self.assertEqual(response.status_code, 201)


class BannerLinkAndFilenameSafetyTests(SimpleTestCase):
    def test_banner_links_reject_javascript_and_credentials(self):
        from .validators import validate_banner_link

        validate_banner_link("/products/trailer-axles")
        validate_banner_link("https://cdn.example.com/campaign")
        validate_banner_link("")
        for unsafe in (
            "javascript:alert(1)",
            "//evil.example/path",
            "https://user:pass@cdn.example.com/x",
            "http://cdn.example.com/x",
            "/path\r\nSet-Cookie: x=1",
        ):
            with self.subTest(link=unsafe):
                with self.assertRaises(ValidationError):
                    validate_banner_link(unsafe)

    def test_upload_filename_is_sanitized(self):
        from .validators import sanitize_upload_filename

        self.assertEqual(
            sanitize_upload_filename("..\\..\\windows\\evil.pdf"),
            "evil.pdf",
        )
        self.assertEqual(
            sanitize_upload_filename("safe\r\nname.pdf"),
            "safe name.pdf",
        )
        self.assertEqual(
            sanitize_upload_filename("drawing.pdf"),
            "drawing.pdf",
        )


class DrfRequestSecurityTests(SimpleTestCase):
    """Exercise request boundaries without writing data or using services."""

    def parse_inquiry_json(self, body: bytes, charset: str = "utf-8"):
        from .views import InquiryCreateView

        # generic() preserves the bytes; post() would encode them with charset.
        raw_request = RequestFactory().generic(
            "POST",
            "/api/v1/inquiries/",
            data=body,
            content_type=f"application/json; charset={charset}",
        )
        return InquiryCreateView().initialize_request(raw_request).data

    def negotiate_product_response(self, accept: str):
        from .views import ProductListView

        view = ProductListView()
        raw_request = RequestFactory().get(
            "/api/v1/products/", HTTP_ACCEPT=accept
        )
        request = view.initialize_request(raw_request)
        view.format_kwarg = None
        return view.perform_content_negotiation(request)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=256)
    def test_json_rejects_compression_charsets_before_expanding_body(self):
        from rest_framework.exceptions import ParseError

        plain_body = json.dumps({"message": "A" * 1024}).encode("utf-8")
        for charset, compress in (
            ("bz2_codec", bz2.compress),
            ("zlib_codec", zlib.compress),
        ):
            with self.subTest(charset=charset):
                compressed_body = compress(plain_body)
                self.assertLess(len(compressed_body), 256)
                with self.assertRaisesMessage(ParseError, "Unsupported charset"):
                    self.parse_inquiry_json(compressed_body, charset)

                # The same bytes are invalid JSON under an ordinary charset.
                with self.assertRaises(ParseError):
                    self.parse_inquiry_json(compressed_body)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=256)
    def test_json_preserves_utf8_and_plaintext_request_size_limit(self):
        from django.core.exceptions import RequestDataTooBig

        payload = {"message": "轴承 enquiry"}
        utf8_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.parse_inquiry_json(utf8_body), payload)

        oversized_body = json.dumps({"message": "A" * 1024}).encode("utf-8")
        with self.assertRaises(RequestDataTooBig):
            self.parse_inquiry_json(oversized_body)

    def test_accept_skips_an_overlong_media_type_but_keeps_a_valid_one(self):
        from rest_framework.exceptions import NotAcceptable

        long_token = "application/json; padding=" + "x" * 256
        with self.assertRaises(NotAcceptable):
            self.negotiate_product_response(long_token)

        renderer, media_type = self.negotiate_product_response(
            long_token + ",application/json"
        )
        self.assertEqual(renderer.media_type, "application/json")
        self.assertEqual(media_type, "application/json")

    def test_accept_does_not_consider_a_match_after_the_token_budget(self):
        from rest_framework.exceptions import NotAcceptable

        # Short, ordinary media types avoid expensive malformed-header parsing.
        unsupported_tokens = [f"text/x-audit-{index}" for index in range(64)]
        with self.assertRaises(NotAcceptable):
            self.negotiate_product_response(
                ",".join([*unsupported_tokens, "application/json"])
            )

        renderer, media_type = self.negotiate_product_response(
            ",".join(["application/json", *unsupported_tokens])
        )
        self.assertEqual(renderer.media_type, "application/json")
        self.assertEqual(media_type, "application/json")

    def test_accept_does_not_consider_a_match_after_the_header_size_budget(self):
        from rest_framework.exceptions import NotAcceptable

        # Stay below the token count/individual size limits to isolate 8 KiB.
        unsupported_tokens = [
            f"text/x-audit-{index}; padding=" + "x" * 224
            for index in range(34)
        ]
        unsupported_header = ",".join(unsupported_tokens)
        self.assertGreater(len(unsupported_header), 8192)
        self.assertLess(max(map(len, unsupported_tokens)), 256)
        with self.assertRaises(NotAcceptable):
            self.negotiate_product_response(
                unsupported_header + ",application/json"
            )

        renderer, media_type = self.negotiate_product_response(
            "application/json," + unsupported_header
        )
        self.assertEqual(renderer.media_type, "application/json")
        self.assertEqual(media_type, "application/json")

    def test_list_validation_errors_keep_positions_inside_the_v1_envelope(self):
        from rest_framework.response import Response

        from .renderers import VersionedJSONRenderer
        from .serializers import InquiryItemSerializer

        serializer = InquiryItemSerializer(
            data=[{"quantity": 1}, {"quantity": "invalid"}], many=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIsInstance(serializer.errors, list)
        self.assertEqual(len(serializer.errors), 2)
        self.assertEqual(serializer.errors[0], {})
        self.assertEqual(serializer.errors[1]["quantity"][0].code, "invalid")

        errors = {"items": serializer.errors}
        response = Response(errors, status=400)
        rendered = VersionedJSONRenderer().render(
            errors,
            renderer_context={
                "request": RequestFactory().get("/api/v1/inquiries/"),
                "response": response,
            },
        )
        payload = json.loads(rendered)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "VALIDATION_ERROR")
        self.assertEqual(payload["fields"], errors)
        self.assertEqual(payload["fields"]["items"][0], {})
        self.assertIsInstance(payload["fields"]["items"], list)


class DrfHttpSecurityTests(TestCase):
    """Exercise middleware, throttles, exception handling and rendered JSON."""

    form_paths = (
        "/api/inquiries/",
        "/api/contact/",
        "/api/v1/inquiries/",
        "/api/v1/contact/",
    )

    def setUp(self):
        cache.clear()
        # Return real 500 responses too, so each regression asserts the HTTP
        # contract rather than stopping at a parser/serializer in isolation.
        self.client = Client(raise_request_exception=False)

    def assert_rejected_without_side_effects(
        self, path, response, expected_status=400
    ):
        self.assertEqual(response.status_code, expected_status, response.content[:400])
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        if path.startswith("/api/v1/"):
            self.assertEqual(
                set(payload), {"success", "error_code", "message", "fields"}
            )
            self.assertFalse(payload["success"])
            self.assertIsInstance(payload["message"], str)
            self.assertIsInstance(payload["fields"], dict)
            self.assertEqual(
                payload["error_code"],
                {400: "VALIDATION_ERROR", 406: "API_ERROR", 415: "UNSUPPORTED_MEDIA_TYPE", 429: "RATE_LIMITED"}[expected_status],
            )
        else:
            self.assertNotIn("success", payload)
        self.assertEqual(Inquiry.objects.count(), 0)
        self.assertEqual(ContactMessage.objects.count(), 0)
        self.assertEqual(InquiryItem.objects.count(), 0)
        self.assertEqual(InquiryAttachment.objects.count(), 0)
        self.assertEqual(AnalyticsEvent.objects.count(), 0)
        self.assertEqual(VisitSession.objects.count(), 0)
        return payload

    def test_non_object_json_reaches_validation_instead_of_crashing_email_throttle(self):
        for path in self.form_paths:
            for value in ([], None, "invalid", 42, True):
                with self.subTest(path=path, value=value):
                    cache.clear()
                    response = self.client.generic(
                        "POST", path, json.dumps(value).encode("utf-8"),
                        content_type="application/json",
                    )
                    self.assert_rejected_without_side_effects(path, response)

    def test_surrogate_email_reaches_field_validation_instead_of_crashing_throttle(self):
        for path in self.form_paths:
            for email in ("\ud800@example.com", "buyer\udfff@example.com"):
                with self.subTest(path=path, email=ascii(email)):
                    cache.clear()
                    with patch("catalog.serializers.verify_turnstile_token") as verify:
                        response = self.client.generic(
                            "POST", path,
                            json.dumps({
                                "name": "Buyer", "email": email,
                                "message": "Please quote a trailer axle.",
                                "privacy_consent": True,
                            }).encode("utf-8"),
                            content_type="application/json",
                        )
                    payload = self.assert_rejected_without_side_effects(path, response)
                    fields = payload["fields"] if path.startswith("/api/v1/") else payload
                    self.assertIn("Surrogate characters are not allowed", fields["email"][0])
                    verify.assert_not_called()

    def test_non_string_email_values_reach_field_validation_without_encoding_errors(self):
        for path in self.form_paths:
            for email in (None, 42, True, [], {}, ["\ud800"], {"value": "\udfff"}):
                with self.subTest(path=path, email=ascii(email)):
                    cache.clear()
                    response = self.client.generic(
                        "POST", path,
                        json.dumps({
                            "name": "Buyer", "email": email,
                            "message": "Please quote a trailer axle.",
                            "privacy_consent": True,
                        }).encode("utf-8"),
                        content_type="application/json",
                    )
                    payload = self.assert_rejected_without_side_effects(path, response)
                    fields = payload["fields"] if path.startswith("/api/v1/") else payload
                    self.assertIsInstance(fields["email"], list)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=256)
    def test_non_text_charsets_are_rejected_through_real_form_views(self):
        plain_body = json.dumps({"message": "A" * 1024}).encode("utf-8")
        for path in self.form_paths:
            for charset, body in (
                ("bz2_codec", bz2.compress(plain_body)),
                ("zlib_codec", zlib.compress(plain_body)),
                ("base64_codec", b"e30="),
                ("hex_codec", b"7b7d"),
                ("rot_13", b"{}"),
            ):
                with self.subTest(path=path, charset=charset):
                    cache.clear()
                    self.assertLess(len(body), 256)
                    with patch("catalog.serializers.verify_turnstile_token") as verify:
                        response = self.client.generic(
                            "POST", path, body,
                            content_type=f"application/json; charset={charset}",
                        )
                    payload = self.assert_rejected_without_side_effects(path, response)
                    message = payload.get("message", payload.get("detail", ""))
                    self.assertIn("Unsupported charset", message)
                    verify.assert_not_called()

    def test_malformed_json_and_unknown_charsets_return_json_errors(self):
        for path in self.form_paths:
            for charset, body in (
                ("utf-8", b'{"message":'),
                ("utf-8", b'{"message":"\xff"}'),
                ("no-such-audit-charset", b'{"message":"\xff"}'),
                ("undefined", b"{}"),
            ):
                with self.subTest(path=path, charset=charset, body=body):
                    cache.clear()
                    response = self.client.generic(
                        "POST", path, body,
                        content_type=f"application/json; charset={charset}",
                    )
                    self.assert_rejected_without_side_effects(path, response)

    def test_default_form_and_multipart_parsers_reject_non_text_charsets(self):
        for path in self.form_paths:
            for content_type, body in (
                ("application/x-www-form-urlencoded; charset=bz2_codec", b"email=a%40example.com"),
                ("multipart/form-data; boundary=audit-boundary; charset=zlib_codec", b"--audit-boundary--\r\n"),
            ):
                with self.subTest(path=path, content_type=content_type):
                    cache.clear()
                    response = self.client.generic(
                        "POST", path, body, content_type=content_type
                    )
                    self.assert_rejected_without_side_effects(path, response)

    def test_content_type_limit_returns_415_without_dispatching_submission(self):
        content_type = "application/json; padding=" + "x" * 256
        for path in self.form_paths:
            with self.subTest(path=path):
                cache.clear()
                response = self.client.generic(
                    "POST", path, b"{}", content_type=content_type
                )
                self.assert_rejected_without_side_effects(path, response, 415)

    def test_accept_limits_return_406_before_body_validation(self):
        overlong_token = "application/json; padding=" + "x" * 256
        unsupported_tokens = [f"text/x-audit-{index}" for index in range(64)]
        unsupported_long_tokens = [
            f"text/x-audit-{index}; padding=" + "x" * 224
            for index in range(34)
        ]
        for path in self.form_paths:
            for accept in (
                overlong_token,
                ",".join([*unsupported_tokens, "application/json"]),
                ",".join([*unsupported_long_tokens, "application/json"]),
            ):
                with self.subTest(path=path, accept_length=len(accept)):
                    cache.clear()
                    with patch("catalog.serializers.verify_turnstile_token") as verify:
                        response = self.client.generic(
                            "POST", path, b"{}", content_type="application/json",
                            HTTP_ACCEPT=accept,
                        )
                    self.assert_rejected_without_side_effects(path, response, 406)
                    verify.assert_not_called()

    def test_usable_accept_after_overlong_token_keeps_field_validation_contract(self):
        for path in self.form_paths:
            with self.subTest(path=path):
                cache.clear()
                response = self.client.generic(
                    "POST", path, b"{}", content_type="application/json",
                    HTTP_ACCEPT="application/json; padding=" + "x" * 256 + ",application/json",
                )
                payload = self.assert_rejected_without_side_effects(path, response)
                fields = payload["fields"] if path.startswith("/api/v1/") else payload
                self.assertIsInstance(fields["name"], list)
                self.assertIsInstance(fields["email"], list)

    def test_invalid_rfq_items_preserve_real_raw_and_versioned_error_shape(self):
        for path in ("/api/inquiries/", "/api/v1/inquiries/"):
            with self.subTest(path=path):
                cache.clear()
                response = self.client.generic(
                    "POST", path,
                    json.dumps({
                        "name": "Buyer", "email": "buyer@example.com",
                        "privacy_consent": True, "items": [None],
                    }).encode("utf-8"),
                    content_type="application/json",
                )
                payload = self.assert_rejected_without_side_effects(path, response)
                fields = payload["fields"] if path.startswith("/api/v1/") else payload
                self.assertEqual(fields["items"], ["Items must be a list of product entries."])

    @override_settings(TURNSTILE_REQUIRED=True, TURNSTILE_SECRET_KEY="test-secret")
    def test_utf8_and_unknown_charset_fallback_preserve_success_and_notification_contract(self):
        # Django ignores an unregistered charset and uses DEFAULT_CHARSET.
        # It must remain safe UTF-8 text, while registered non-text codecs fail.
        with (
            patch("catalog.serializers.verify_turnstile_token") as verify,
            patch("catalog.views.enqueue_inquiry_notification") as inquiry_queue,
            patch("catalog.views.enqueue_contact_notification") as contact_queue,
        ):
            for path in self.form_paths:
                for charset in ("utf-8", "no-such-audit-charset"):
                    with self.subTest(path=path, charset=charset):
                        cache.clear()
                        email = f"review-{verify.call_count}@example.com"
                        body = json.dumps({
                            "name": "轴承 Buyer", "email": email,
                            "message": "Please quote a trailer axle.",
                            "privacy_consent": True, "turnstile_token": "test-token",
                        }, ensure_ascii=False).encode("utf-8")
                        with self.captureOnCommitCallbacks(execute=True):
                            response = self.client.generic(
                                "POST", path, body,
                                content_type=f"application/json; charset={charset}",
                            )
                        self.assertEqual(response.status_code, 201, response.content[:400])
                        payload = response.json()
                        if path.startswith("/api/v1/"):
                            self.assertEqual(set(payload), {"success", "data", "message"})
                            self.assertTrue(payload["success"])
                            reference = payload["data"]["reference"]
                        else:
                            self.assertEqual(set(payload), {"reference", "message"})
                            reference = payload["reference"]
                        model = Inquiry if "inquiries" in path else ContactMessage
                        instance = model.objects.get(email=email)
                        self.assertEqual(instance.reference, reference)
                        self.assertEqual(instance.name, "轴承 Buyer")
                        self.assertEqual(verify.call_args.kwargs["expected_action"], "rfq" if "inquiries" in path else "contact")
            self.assertEqual(verify.call_count, 8)
            self.assertEqual(inquiry_queue.call_count, 4)
            self.assertEqual(contact_queue.call_count, 4)

    def test_existing_ip_throttle_still_rejects_parsed_valid_requests_without_writes(self):
        for path in self.form_paths:
            with self.subTest(path=path):
                cache.clear()
                throttle_class = RFQIpThrottle if "inquiries" in path else ContactIpThrottle
                with patch.dict(throttle_class.THROTTLE_RATES, {throttle_class.scope: "1/hour"}):
                    first = self.client.generic(
                        "POST", path, b"{}", content_type="application/json"
                    )
                    self.assert_rejected_without_side_effects(path, first)
                    second = self.client.generic(
                        "POST", path, b"{}", content_type="application/json"
                    )
                    self.assert_rejected_without_side_effects(path, second, 429)
                    self.assertGreater(int(second["Retry-After"]), 0)

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=256)
    def test_plaintext_body_limit_returns_400_without_writes_through_http_stack(self):
        body = json.dumps({"message": "A" * 1024}).encode("utf-8")
        for path in self.form_paths:
            with self.subTest(path=path):
                cache.clear()
                with patch("catalog.serializers.verify_turnstile_token") as verify:
                    response = self.client.generic(
                        "POST", path, body, content_type="application/json"
                    )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(Inquiry.objects.count(), 0)
                self.assertEqual(ContactMessage.objects.count(), 0)
                self.assertEqual(AnalyticsEvent.objects.count(), 0)
                verify.assert_not_called()

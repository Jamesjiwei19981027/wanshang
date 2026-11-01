from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import serializers
from rest_framework.test import APIClient

from .models import Category
from .serializers import InquirySerializer
from .spec_templates import TRAILER_AXLE_PARAMETERS, apply_template
from .specification_rules import (
    compact_decimal,
    parse_dimension_value,
    parse_positive_number,
)


class BoundedSpecificationNumberTests(SimpleTestCase):
    def test_expansion_bombs_are_rejected_before_fixed_point_formatting(self):
        # Patch the allocation boundary so this regression test cannot allocate
        # the million-character strings even if validation regresses.
        for candidate in (
            "1e-1000000",
            "1e1000000",
            "0e-1000000",
            "NaN",
            "sNaN",
            "Infinity",
            "-Infinity",
        ):
            with self.subTest(candidate=candidate), patch(
                "catalog.specification_rules.format",
                side_effect=AssertionError("Unsafe number reached fixed-point formatting"),
                create=True,
            ) as render:
                with self.assertRaises(ValueError):
                    compact_decimal(Decimal(candidate))
                render.assert_not_called()

    def test_numeric_parser_rejects_short_inputs_with_extreme_exponents(self):
        for candidate in ("1e-1000000", "1e1000000", "1e-999999999999999999999"):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                parse_positive_number(candidate, maximum=Decimal("100000000"))

    def test_fixed_point_length_is_bounded_at_both_ends(self):
        self.assertEqual(len(compact_decimal(Decimal("1e-158"))), 160)
        self.assertEqual(len(compact_decimal(Decimal("1e159"))), 160)
        self.assertEqual(len(compact_decimal(Decimal("-1e158"))), 160)
        for candidate in ("1e-159", "1e160", "-1e159", "1" * 161):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                compact_decimal(Decimal(candidate))

    def test_parsers_bound_input_before_constructing_a_decimal(self):
        with patch(
            "catalog.specification_rules.Decimal",
            side_effect=AssertionError("Oversized input reached Decimal construction"),
        ) as constructor:
            with self.assertRaises(ValueError):
                parse_positive_number("1" * 161, maximum=Decimal("100000000"))
            with self.assertRaises(ValueError):
                parse_dimension_value("1" * 161)
            constructor.assert_not_called()



def axle_category() -> Category:
    category = Category.objects.create(name="Trailer Axles", slug="trailer-axles")
    apply_template(category, TRAILER_AXLE_PARAMETERS)
    return category


class RFQParameterNormalizationTests(TestCase):
    def setUp(self):
        axle_category()

    def normalize(self, values: dict) -> dict:
        return InquirySerializer()._normalize_specifications({"trailer-axles": values}, ["trailer-axles"])

    def test_regular_business_numbers_and_units_are_normalized(self):
        normalized = self.normalize(
            {
                "rated_capacity": "3,500",
                "trailer_weight": "10,000.2500 lb",
                "hub_face": "95.500 inches",
                "drop_height": ".125 in",
                "brake_type": "electric drum",
            }
        )["trailer-axles"]
        self.assertEqual(normalized["rated_capacity"]["value"], "3,500 lb")
        self.assertEqual(normalized["trailer_weight"]["value"], "10,000.25 lb")
        self.assertEqual(normalized["hub_face"]["value"], "95.5 in")
        self.assertEqual(normalized["drop_height"]["value"], "0.125 in")
        self.assertEqual(normalized["drop_height"]["number"], "0.125")
        self.assertEqual(normalized["brake_type"]["value"], "Electric drum")

    def test_metric_entries_keep_their_unit(self):
        normalized = self.normalize({"rated_capacity": "1588 kg", "hub_face": "2413mm"})["trailer-axles"]
        self.assertEqual(normalized["rated_capacity"]["unit"], "kg")
        self.assertEqual(normalized["hub_face"]["unit"], "mm")

    def test_unknown_or_unasked_parameters_and_wrong_units_are_rejected(self):
        for values in (
            {"inner_bearing": "L68149"},  # not asked in the RFQ
            {"not_a_parameter": "x"},
            {"rated_capacity": "95 mm"},
            {"rated_capacity": "-5"},
            {"brake_type": "rocket"},
            {"rated_capacity": "1e-1000000"},
        ):
            with self.subTest(values=values), self.assertRaises(serializers.ValidationError):
                self.normalize(values)

    def test_parameters_require_the_category_to_be_selected(self):
        with self.assertRaises(serializers.ValidationError):
            InquirySerializer()._normalize_specifications({"trailer-axles": {"rated_capacity": "3500"}}, [])


@override_settings(
    TURNSTILE_REQUIRED=True,
    TURNSTILE_SECRET_KEY="isolated-specification-security-tests",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "specification-security-tests",
        },
    },
)
class PublicSpecificationSecurityTests(TestCase):
    def test_rfq_endpoints_return_field_errors_without_captcha_work(self):
        axle_category()
        client = APIClient()
        for path in ("/api/inquiries/", "/api/v1/inquiries/"):
            for key in ("rated_capacity", "trailer_weight"):
                with self.subTest(path=path, key=key), patch("catalog.serializers.verify_turnstile_token") as verify:
                    cache.clear()
                    response = client.post(
                        path,
                        {
                            "name": "Security Regression Buyer",
                            "email": "security-regression@example.com",
                            "privacy_consent": True,
                            "requested_categories": ["trailer-axles"],
                            "specifications": {"trailer-axles": {key: "1e-1000000"}},
                        },
                        format="json",
                    )
                    self.assertEqual(response.status_code, 400)
                    body = response.json()
                    errors = body.get("specifications") or body.get("fields", {}).get("specifications")
                    self.assertIn(f"trailer-axles.{key}", errors)
                    verify.assert_not_called()

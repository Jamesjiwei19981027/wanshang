from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework import serializers
from rest_framework.test import APIClient

from .serializers import InquirySerializer
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

    def test_regular_business_numbers_and_scientific_notation_are_preserved(self):
        validator = InquirySerializer().validate_specifications
        self.assertEqual(
            validator(
                {
                    "capacity": "3.5000e3",
                    "estimated_trailer_weight": "10,000.2500",
                    "hub_face": "95.500 inches",
                    "drop_height": ".125 in",
                }
            ),
            {
                "capacity": "3500",
                "estimated_trailer_weight": "10000.25",
                "hub_face": "95.5 in",
                "drop_height": "0.125 in",
            },
        )
        self.assertEqual(validator({"capacity": "1e8"}), {"capacity": "100000000"})
        with self.assertRaises(serializers.ValidationError):
            validator({"capacity": "1.00000001e8"})


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
class PublicSpecificationSecurityTests(SimpleTestCase):
    def test_rfq_endpoints_return_field_errors_without_formatting_or_captcha_work(self):
        client = APIClient()
        for path in ("/api/inquiries/", "/api/v1/inquiries/"):
            for key in ("capacity", "estimated_trailer_weight"):
                with self.subTest(path=path, key=key), patch(
                    "catalog.specification_rules.format",
                    side_effect=AssertionError("Unsafe number reached fixed-point formatting"),
                    create=True,
                ) as render, patch("catalog.serializers.verify_turnstile_token") as verify:
                    cache.clear()
                    response = client.post(
                        path,
                        {
                            "name": "Security Regression Buyer",
                            "email": "security-regression@example.com",
                            "privacy_consent": True,
                            "specifications": {key: "1e-1000000"},
                        },
                        format="json",
                    )
                    self.assertEqual(response.status_code, 400)
                    self.assertIn(key, response.data["specifications"])
                    render.assert_not_called()
                    verify.assert_not_called()

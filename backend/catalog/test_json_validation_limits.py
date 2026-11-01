from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from .serializers import AnalyticsEventSerializer, encode_safe_public_json


class UnvisitedList(list):
    def __iter__(self):
        raise AssertionError("An over-budget array was expanded before rejection")


class UnvisitedDict(dict):
    def __iter__(self):
        # Disable dict(subclass)'s plain-dict fast path so a defensive copy
        # must use keys(), which makes the pre-copy bound observable below.
        return super().__iter__()

    def items(self):
        raise AssertionError("An over-budget object was expanded before rejection")

    def keys(self):
        raise AssertionError("An over-budget object was copied before rejection")


class PublicJsonBudgetTests(SimpleTestCase):
    def encode(self, value):
        return encode_safe_public_json(value, label="Test JSON", maximum_length=4_000)

    def test_wide_arrays_are_rejected_before_expanding_children(self):
        with self.assertRaisesMessage(ValidationError, "too many values"):
            self.encode({"values": UnvisitedList([0] * 2_000)})

    def test_wide_objects_are_rejected_before_expanding_children(self):
        with self.assertRaisesMessage(ValidationError, "too many values"):
            self.encode(UnvisitedDict({str(index): 0 for index in range(2_000)}))

    def test_pending_siblings_count_toward_budget_before_array_expansion(self):
        # Neither branch is wide in isolation, but together they exceed 500
        # nodes. The second branch must be rejected without iterating it.
        with self.assertRaisesMessage(ValidationError, "too many values"):
            self.encode([UnvisitedList([0] * 249), [0] * 249])

    def test_pending_siblings_count_toward_budget_before_object_expansion(self):
        with self.assertRaisesMessage(ValidationError, "too many values"):
            self.encode([UnvisitedDict({str(index): 0 for index in range(249)}), [0] * 249])

    def test_exact_node_budget_preserves_valid_json(self):
        self.assertEqual(self.encode([0] * 499), "[" + ",".join(["0"] * 499) + "]")
        self.assertEqual(self.encode([[0] * 248, [0] * 249]), "[[" + ",".join(["0"] * 248) + "],[" + ",".join(["0"] * 249) + "]]")
        with self.assertRaisesMessage(ValidationError, "too many values"):
            self.encode([0] * 500)

    def test_depth_nonfinite_and_invalid_unicode_guards_remain_active(self):
        nested = 0
        for _ in range(9):
            nested = [nested]
        for value, expected in (
            (nested, "nested too deeply"),
            ({"value": float("inf")}, "non-finite"),
            ({"value": "bad\x00text"}, "Null and unpaired surrogate"),
            ({"value": "\ud800"}, "Null and unpaired surrogate"),
        ):
            with self.subTest(expected=expected), self.assertRaisesMessage(ValidationError, expected):
                self.encode(value)

    def test_long_strings_and_keys_are_rejected_before_encoding(self):
        for value in ({"long": "x" * 4_001}, {"x" * 4_001: 0}):
            with self.subTest(key=next(iter(value))[:10]), patch(
                "catalog.serializers.json.dumps",
                side_effect=AssertionError("Oversized string reached JSON encoding"),
            ) as encoder:
                with self.assertRaisesMessage(ValidationError, "at most 4,000 characters"):
                    self.encode(value)
                encoder.assert_not_called()

    def test_metadata_is_bounded_before_defensive_copy(self):
        metadata = UnvisitedDict({str(index): 0 for index in range(2_000)})
        with self.assertRaisesMessage(ValidationError, "too many values"):
            AnalyticsEventSerializer().validate_metadata(metadata)

    def test_server_marker_is_removed_without_modifying_input_at_node_boundary(self):
        metadata = {str(index): 0 for index in range(499)}
        metadata["server_side"] = True
        self.assertEqual(len(AnalyticsEventSerializer().validate_metadata(metadata)), 499)
        self.assertTrue(metadata["server_side"])


@override_settings(ALLOWED_HOSTS=["testserver"])
class PublicJsonHttpBudgetTests(SimpleTestCase):
    def test_public_event_endpoints_reject_wide_metadata_before_saving(self):
        client = APIClient()
        for path in ("/api/events/", "/api/v1/events/"):
            for metadata in ({"values": [0] * 30_000}, {str(index): 0 for index in range(2_000)}):
                with self.subTest(path=path, metadata_type=type(next(iter(metadata.values()))).__name__), patch(
                    "catalog.serializers.AnalyticsEvent.objects.create",
                ) as create:
                    cache.clear()
                    response = client.post(path, {
                        "event_name": "page_view", "path": "/", "metadata": metadata,
                    }, format="json")
                    self.assertEqual(response.status_code, 400, response.content[:500])
                    errors = response.json()
                    if path.startswith("/api/v1/"):
                        self.assertFalse(errors["success"])
                        errors = errors["fields"]
                    self.assertIn("metadata", errors)
                    create.assert_not_called()

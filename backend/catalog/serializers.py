from __future__ import annotations

import json
import math
import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import ProhibitNullCharactersValidator
from django.urls import reverse
from rest_framework import serializers
from rest_framework.validators import ProhibitSurrogateCharactersValidator

from .attribution import build_submission_attribution, upsert_visit_session
from .file_security import (
    AttachmentSecurityError,
    schedule_inquiry_attachment_scan,
    validate_attachment_content,
)
from .models import (
    AnalyticsEvent, Application, AxleSpec, Banner, Category, ContactMessage,
    CompanyPhoto, CompanyProfile,
    Inquiry, InquiryAttachment, InquiryItem, PageView, Product, ProductDocument,
    ProductImage, NewsArticle, SiteSetting, VisitSession,
)
from .specification_rules import (
    BRAKE_ALIASES,
    SURFACE_FINISH_ALIASES,
    TUBE_SHAPE_ALIASES,
    compact_decimal,
    normalize_choice_value,
    parse_dimension_value,
    parse_positive_number,
)
from .turnstile import TurnstileVerificationError, verify_turnstile_token
from .validators import (
    sanitize_upload_filename,
    secure_public_url_or_blank,
    validate_secure_public_url,
)


SESSION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")
PHONE_PATTERN = re.compile(r"^[0-9+().\-\s]{6,60}$")
RFQ_APPLICATIONS = {
    "utility-trailers",
    "boat-trailers",
    "agricultural-trailers",
    "car-haulers",
    "rv-caravan",
    "construction-trailers",
    "heavy-duty-transport",
    "other",
}
RFQ_SPECIFICATION_KEYS = frozenset(
    {
        "estimated_trailer_weight",
        "capacity",
        "capacity_unit",
        "axle_count",
        "axle_type",
        "brake",
        "environment",
        "hub_face",
        "spring_center",
        "overall_length",
        "tube_shape",
        "tube_size",
        "drop_height",
        "dimension_unit",
        "bolt_pattern",
        "stud_size",
        "hub_type",
        "bearing_type",
        "brake_size",
        "finish",
        "oem_requirement",
        "odm_requirement",
        "special_requirements",
        # ProductInquiryForm submits this human-readable convenience value;
        # the authoritative historical link remains items[].product_id/snapshot.
        "selected_product",
    }
)
RFQ_NUMERIC_SPECIFICATION_KEYS = frozenset(
    {"estimated_trailer_weight", "capacity"}
)
RFQ_DIMENSION_SPECIFICATION_KEYS = frozenset(
    {"hub_face", "spring_center", "overall_length", "drop_height"}
)
RFQ_LONG_SPECIFICATION_KEYS = frozenset({"special_requirements"})
RFQ_AXLE_COUNTS = ["single", "tandem", "triple", "unknown"]
RFQ_REQUIREMENT_CHOICES = ["yes", "no", "discuss"]
POSITIVE_DECIMAL_INTEGER_PATTERN = re.compile(r"^[0-9]+$")
MAX_BIG_AUTO_FIELD_VALUE = 9_223_372_036_854_775_807
MAX_PUBLIC_JSON_NODES = 500
_prohibit_null_characters = ProhibitNullCharactersValidator()
_prohibit_surrogate_characters = ProhibitSurrogateCharactersValidator()


def parse_strict_positive_integer(
    value: object,
    *,
    label: str,
    maximum: int,
) -> int:
    """Accept only an integer or an unadorned ASCII decimal integer string."""
    if isinstance(value, bool):
        raise serializers.ValidationError(f"{label} must be a positive integer.")
    if isinstance(value, int):
        number = value
    elif (
        isinstance(value, str)
        and len(value) <= 19
        and POSITIVE_DECIMAL_INTEGER_PATTERN.fullmatch(value)
    ):
        number = int(value)
    else:
        raise serializers.ValidationError(f"{label} must be a positive integer.")
    if number < 1 or number > maximum:
        raise serializers.ValidationError(
            f"{label} must be between 1 and {maximum:,}."
        )
    return number


def validate_public_text(value: str) -> str:
    """Reject text that cannot be stored safely in PostgreSQL JSONB."""
    try:
        _prohibit_null_characters(value)
        _prohibit_surrogate_characters(value)
    except (DjangoValidationError, serializers.ValidationError) as exc:
        raise ValueError(
            "Null and unpaired surrogate characters are not accepted."
        ) from exc
    return value


def encode_safe_public_json(
    value: object,
    *,
    label: str,
    maximum_length: int,
) -> str:
    """Bound arbitrary public JSON before handing it to a database JSON field."""
    stack: list[tuple[object, int]] = [(value, 0)]
    node_count = 0
    while stack:
        current, depth = stack.pop()
        node_count += 1
        if node_count > MAX_PUBLIC_JSON_NODES:
            raise serializers.ValidationError(f"{label} has too many values.")
        if depth > 8:
            raise serializers.ValidationError(f"{label} is nested too deeply.")
        if isinstance(current, str):
            if len(current) > maximum_length:
                raise serializers.ValidationError(
                    f"{label} must contain at most {maximum_length:,} characters."
                )
            try:
                validate_public_text(current)
            except ValueError as exc:
                raise serializers.ValidationError(f"{label}: {exc}") from exc
        elif isinstance(current, float):
            if not math.isfinite(current):
                raise serializers.ValidationError(
                    f"{label} contains a non-finite number."
                )
        elif current is None or isinstance(current, (bool, int)):
            continue
        elif isinstance(current, dict):
            # Account for queued siblings as well as visited values before
            # adding children. An invalid wide object must never build an
            # unbounded validation stack before the node check can run.
            if len(current) > MAX_PUBLIC_JSON_NODES - node_count - len(stack):
                raise serializers.ValidationError(f"{label} has too many values.")
            for key, nested_value in current.items():
                if not isinstance(key, str):
                    raise serializers.ValidationError(
                        f"{label} keys must be strings."
                    )
                try:
                    if len(key) > maximum_length:
                        raise serializers.ValidationError(
                            f"{label} must contain at most {maximum_length:,} characters."
                        )
                    validate_public_text(key)
                except ValueError as exc:
                    raise serializers.ValidationError(f"{label}: {exc}") from exc
                stack.append((nested_value, depth + 1))
        elif isinstance(current, list):
            if len(current) > MAX_PUBLIC_JSON_NODES - node_count - len(stack):
                raise serializers.ValidationError(f"{label} has too many values.")
            stack.extend((nested_value, depth + 1) for nested_value in current)
        else:
            raise serializers.ValidationError(
                f"{label} contains an unsupported value."
            )
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (OverflowError, RecursionError, TypeError, ValueError) as exc:
        raise serializers.ValidationError(f"{label} must be valid JSON.") from exc
    if len(encoded) > maximum_length:
        raise serializers.ValidationError(
            f"{label} must contain at most {maximum_length:,} characters."
        )
    return encoded


def validate_session_key(value: str) -> str:
    value = value.strip()
    if not SESSION_KEY_PATTERN.fullmatch(value):
        raise serializers.ValidationError("Session identifiers must be 8-100 safe characters.")
    return value


def validate_phone_number(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    digit_count = sum(character.isdigit() for character in value)
    if not PHONE_PATTERN.fullmatch(value) or not 6 <= digit_count <= 20:
        raise serializers.ValidationError("Enter a valid international phone number.")
    return value


class SecureExternalURLField(serializers.URLField):
    """Validate writes and fail closed when rendering external public URLs."""

    def __init__(self, **kwargs):
        validators = list(kwargs.pop("validators", ()))
        validators.append(validate_secure_public_url)
        super().__init__(validators=validators, **kwargs)

    def to_representation(self, value) -> str:
        return secure_public_url_or_blank(super().to_representation(value))


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True)
    image_url = SecureExternalURLField(required=False, allow_blank=True)

    class Meta:
        model = Category
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "icon",
            "image_url",
            "product_count",
            "meta_title",
            "meta_description",
        )


class ApplicationSerializer(serializers.ModelSerializer):
    image_url = SecureExternalURLField(required=False, allow_blank=True)

    class Meta:
        model = Application
        fields = ("id", "name", "slug", "summary", "image_url", "recommended_finish")


class AxleSpecSerializer(serializers.ModelSerializer):
    class Meta:
        model = AxleSpec
        exclude = ("id", "product", "created_at", "updated_at")


class ProductDocumentSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    product_title = serializers.CharField(source="product.title", read_only=True)
    product_model = serializers.CharField(source="product.model", read_only=True)

    class Meta:
        model = ProductDocument
        fields = (
            "id",
            "title",
            "document_type",
            "version",
            "file_format",
            "file_size",
            "updated_at",
            "product_id",
            "product_title",
            "product_model",
            "url",
            "previewable",
        )
        read_only_fields = (
            "file_format",
            "file_size",
            "updated_at",
            "product_id",
            "previewable",
        )

    def get_url(self, obj: ProductDocument) -> str:
        request = self.context.get("request")
        path = reverse("document-download", kwargs={"pk": obj.pk})
        return request.build_absolute_uri(path) if request else path


def public_media_url(obj, field_name: str, external_field: str, request) -> str:
    field = getattr(obj, field_name)
    if field and field.name:
        url = field.url
        return request.build_absolute_uri(url) if request else url
    return secure_public_url_or_blank(getattr(obj, external_field, ""))


class ProductImageSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = (
            "id",
            "image_url",
            "image_type",
            "alt_text",
            "caption",
            "sort_order",
        )

    def get_image_url(self, obj: ProductImage) -> str:
        return public_media_url(
            obj,
            "image",
            "external_url",
            self.context.get("request"),
        )


class ProductListSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    applications = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id", "title", "slug", "model", "sku", "short_description", "image_url",
            "rated_capacity", "capacity_unit", "dimension_unit", "axle_type",
            "brake_type", "bolt_pattern", "stud_size", "grease_cap_type", "tube_size",
            "hub_face_length", "spring_center", "surface_finish", "is_customizable",
            "oem_available", "made_to_order", "packaging", "featured", "category",
            "applications", "updated_at",
        )

    def get_applications(self, obj: Product) -> list[dict]:
        applications = getattr(obj, "active_applications", None)
        if applications is None:
            applications = obj.applications.filter(is_active=True)
        return ApplicationSerializer(applications, many=True).data

    def get_image_url(self, obj: Product) -> str:
        if direct_url := secure_public_url_or_blank(obj.image_url):
            return direct_url
        images = getattr(obj, "card_images", None)
        if images is None:
            images = obj.images.all()
        first_image = next(iter(images), None)
        if first_image is None:
            return ""
        return public_media_url(
            first_image,
            "image",
            "external_url",
            self.context.get("request"),
        )


class ProductDetailSerializer(ProductListSerializer):
    axle_spec = AxleSpecSerializer(read_only=True)
    documents = ProductDocumentSerializer(many=True, read_only=True, source="active_documents")
    images = ProductImageSerializer(many=True, read_only=True)

    class Meta(ProductListSerializer.Meta):
        fields = ProductListSerializer.Meta.fields + (
            "description",
            "axle_spec",
            "images",
            "documents",
            "meta_title",
            "meta_description",
        )


class NewsArticleListSerializer(serializers.ModelSerializer):
    cover_image_url = serializers.SerializerMethodField()

    class Meta:
        model = NewsArticle
        fields = (
            "id",
            "title",
            "slug",
            "summary",
            "category",
            "tags",
            "reading_minutes",
            "cover_image_url",
            "author",
            "is_featured",
            "published_at",
            "seo_title",
            "seo_description",
        )

    def get_cover_image_url(self, obj: NewsArticle) -> str:
        return public_media_url(
            obj,
            "cover_image",
            "cover_image_url",
            self.context.get("request"),
        )


class NewsArticleDetailSerializer(NewsArticleListSerializer):
    class Meta(NewsArticleListSerializer.Meta):
        fields = NewsArticleListSerializer.Meta.fields + (
            "body",
            "seo_keywords",
            "updated_at",
        )


class BannerSerializer(serializers.ModelSerializer):
    desktop_image_url = serializers.SerializerMethodField()
    mobile_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Banner
        fields = (
            "id",
            "title",
            "subtitle",
            "desktop_image_url",
            "mobile_image_url",
            "button_text",
            "button_link",
            "secondary_button_text",
            "secondary_button_link",
        )

    def get_desktop_image_url(self, obj: Banner) -> str:
        return public_media_url(
            obj,
            "desktop_image",
            "desktop_image_url",
            self.context.get("request"),
        )

    def get_mobile_image_url(self, obj: Banner) -> str:
        return public_media_url(
            obj,
            "mobile_image",
            "mobile_image_url",
            self.context.get("request"),
        )


class SiteSettingSerializer(serializers.ModelSerializer):
    site_logo_url = serializers.SerializerMethodField()
    default_og_image_url = serializers.SerializerMethodField()
    whatsapp_link = SecureExternalURLField(required=False, allow_blank=True)
    map_url = SecureExternalURLField(required=False, allow_blank=True)
    linkedin_url = SecureExternalURLField(required=False, allow_blank=True)
    website_url = SecureExternalURLField(required=False, allow_blank=True)

    class Meta:
        model = SiteSetting
        fields = (
            "site_name",
            "legal_name",
            "chinese_name",
            "tagline",
            "site_logo_url",
            "contact_email",
            "phone_number",
            "contact_person",
            "contact_title",
            "website_url",
            "whatsapp_number",
            "whatsapp_link",
            "address",
            "business_hours",
            "map_url",
            "linkedin_url",
            "global_seo_title",
            "global_seo_keywords",
            "global_seo_description",
            "default_og_image_url",
            "google_analytics_id",
            "google_tag_manager_id",
            "footer_text",
        )

    def get_site_logo_url(self, obj: SiteSetting) -> str:
        return public_media_url(
            obj,
            "site_logo",
            "site_logo_url",
            self.context.get("request"),
        )

    def get_default_og_image_url(self, obj: SiteSetting) -> str:
        return public_media_url(
            obj,
            "default_og_image",
            "default_og_image_url",
            self.context.get("request"),
        )


class CompanyPhotoSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = CompanyPhoto
        fields = ("id", "image_url", "alt_text", "caption")

    def get_image_url(self, obj) -> str:
        return public_media_url(obj, "image", "", self.context.get("request"))


class CompanyProfileSerializer(serializers.ModelSerializer):
    photos = CompanyPhotoSerializer(many=True, read_only=True)
    business_role_label = serializers.CharField(source="get_business_role_display", read_only=True)

    class Meta:
        model = CompanyProfile
        fields = ("headline", "summary", "business_role", "business_role_label", "introduction", "main_products", "markets", "strengths", "quality_information", "meta_title", "meta_description", "photos")


class VisitSessionSerializer(serializers.ModelSerializer):
    session_id = serializers.CharField(
        write_only=True,
        required=False,
        max_length=100,
    )
    session_key = serializers.CharField(
        write_only=True,
        required=False,
        max_length=100,
    )

    class Meta:
        model = VisitSession
        fields = (
            "session_id",
            "session_key",
            "country",
            "city",
            "first_touch_source",
            "last_touch_source",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "referrer_url",
            "first_landing_page",
            "last_landing_page",
            "device_type",
            "browser",
            "os",
            "consent_status",
            "started_at",
            "last_seen_at",
            "converted_at",
        )
        read_only_fields = (
            "country",
            "city",
            "device_type",
            "browser",
            "os",
            "started_at",
            "last_seen_at",
            "converted_at",
        )

    def validate_session_key(self, value: str) -> str:
        return validate_session_key(value)

    def validate_session_id(self, value: str) -> str:
        return validate_session_key(value)

    def validate(self, attrs: dict) -> dict:
        session_id = attrs.pop("session_id", "")
        session_key = attrs.get("session_key", "")
        if not session_id and not session_key:
            raise serializers.ValidationError(
                {"session_id": "A session identifier is required."}
            )
        if session_id and session_key and session_id != session_key:
            raise serializers.ValidationError(
                {"session_id": "Session identifiers do not match."}
            )
        attrs["session_key"] = session_id or session_key
        return attrs

    def create(self, validated_data: dict) -> VisitSession:
        session_key = validated_data.pop("session_key")
        return upsert_visit_session(
            session_key,
            validated_data,
            self.context["request"],
        )


class PageViewSerializer(serializers.ModelSerializer):
    session_id = serializers.CharField(write_only=True, max_length=100)
    product_id = serializers.PrimaryKeyRelatedField(
        pk_field=serializers.IntegerField(min_value=1, max_value=MAX_BIG_AUTO_FIELD_VALUE),
        source="product",
        queryset=Product.objects.filter(
            status=Product.Status.PUBLISHED,
            category__is_active=True,
        ),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = PageView
        fields = ("session_id", "path", "page_title", "product_id", "timestamp")
        read_only_fields = ("timestamp",)

    def validate_session_id(self, value: str) -> str:
        return validate_session_key(value)

    def create(self, validated_data: dict) -> PageView:
        session_key = validated_data.pop("session_id")
        path = validated_data["path"]
        session = upsert_visit_session(
            session_key,
            {
                "first_landing_page": path,
                "last_landing_page": path,
            },
            self.context["request"],
        )
        return PageView.objects.create(session=session, **validated_data)


class InquiryAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = InquiryAttachment
        fields = ("id", "original_name", "size", "created_at")


class InquiryItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = InquiryItem
        fields = (
            "id",
            "product",
            "product_snapshot",
            "quantity",
            "selected_options",
            "requested_specs",
        )


class InquirySerializer(serializers.ModelSerializer):
    # JSONField accepts JSON posted in either an application/json body or multipart
    # form data, which is necessary when an RFQ includes uploaded drawings.
    items = serializers.JSONField(write_only=True, required=False)
    quantity = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        max_value=100_000_000,
    )
    specifications = serializers.JSONField(required=False, default=dict)
    website = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    session_id = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=100,
        default="",
    )
    turnstile_token = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=2048,
        default="",
    )
    referrer_url = serializers.URLField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=1000,
        default="",
    )
    attachments = InquiryAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Inquiry
        fields = (
            "reference", "name", "email", "phone", "company", "country", "quantity",
            "application", "destination_port", "target_delivery_date", "message",
            "specifications", "privacy_consent", "items", "website",
            "session_id", "turnstile_token", "attachments", "source_url",
            "referrer_url",
            "first_touch_source", "last_touch_source", "utm_source", "utm_medium",
            "utm_campaign", "utm_term", "utm_content", "first_landing_page",
            "last_landing_page", "created_at",
        )
        read_only_fields = ("reference", "attachments", "created_at")
        extra_kwargs = {
            "message": {"max_length": 10_000},
            "source_url": {"write_only": True},
            "utm_source": {"write_only": True},
            "utm_medium": {"write_only": True},
            "utm_campaign": {"write_only": True},
            "utm_term": {"write_only": True},
            "utm_content": {"write_only": True},
            "first_touch_source": {"write_only": True},
            "last_touch_source": {"write_only": True},
            "first_landing_page": {"write_only": True},
            "last_landing_page": {"write_only": True},
        }

    def validate_session_id(self, value: str) -> str:
        return validate_session_key(value) if value else ""

    def validate_phone(self, value: str) -> str:
        return validate_phone_number(value)

    def validate_application(self, value: str) -> str:
        value = value.strip()
        if value and value not in RFQ_APPLICATIONS:
            raise serializers.ValidationError("Select a supported application or Other.")
        return value

    def validate_specifications(self, value: object) -> dict:
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                "Technical specifications must be an object."
            )
        unknown_keys = [
            key for key in value if not isinstance(key, str) or key not in RFQ_SPECIFICATION_KEYS
        ]
        if unknown_keys:
            rendered_keys = ", ".join(sorted(repr(key) for key in unknown_keys))
            raise serializers.ValidationError(
                {"unknown_keys": f"Unsupported specification fields: {rendered_keys}."}
            )

        normalized: dict[str, str] = {}
        errors: dict[str, str] = {}
        explicit_dimension_units: dict[str, str] = {}
        for key, raw_value in value.items():
            if (
                raw_value is None
                or isinstance(raw_value, bool)
                or not isinstance(raw_value, (str, int, float))
            ):
                errors[key] = "Use a string or number; nested, boolean and null values are not accepted."
                continue
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                errors[key] = "Use a finite value."
                continue

            rendered = str(raw_value).strip()
            try:
                validate_public_text(rendered)
            except ValueError as exc:
                errors[key] = str(exc)
                continue
            maximum_length = 4_000 if key in RFQ_LONG_SPECIFICATION_KEYS else 160
            if len(rendered) > maximum_length:
                errors[key] = f"Ensure this value has at most {maximum_length} characters."
                continue
            if not rendered:
                normalized[key] = ""
                continue

            try:
                if key in RFQ_NUMERIC_SPECIFICATION_KEYS:
                    number = parse_positive_number(
                        rendered,
                        maximum=Decimal("100000000"),
                        allow_commas=True,
                    )
                    normalized[key] = compact_decimal(number)
                elif key in RFQ_DIMENSION_SPECIFICATION_KEYS:
                    number, unit = parse_dimension_value(rendered)
                    if number > Decimal("99999999.99"):
                        raise ValueError("Dimension is out of range.")
                    normalized[key] = compact_decimal(number)
                    if unit:
                        explicit_dimension_units[key] = unit
                        normalized[key] += f" {unit}"
                elif key == "capacity_unit":
                    choice = normalize_choice_value(
                        rendered,
                        aliases={},
                        allowed=Product.CapacityUnit.values,
                    )
                    if choice is None:
                        raise ValueError("Capacity unit must be lb or kg.")
                    normalized[key] = choice
                elif key == "dimension_unit":
                    choice = normalize_choice_value(
                        rendered,
                        aliases={},
                        allowed=Product.DimensionUnit.values,
                    )
                    if choice is None:
                        raise ValueError("Dimension unit must be in or mm.")
                    normalized[key] = choice
                elif key == "axle_count":
                    choice = normalize_choice_value(
                        rendered,
                        aliases={},
                        allowed=RFQ_AXLE_COUNTS,
                    )
                    if choice is None:
                        raise ValueError("Select single, tandem, triple or unknown.")
                    normalized[key] = choice
                elif key == "axle_type":
                    choice = normalize_choice_value(
                        rendered,
                        aliases={},
                        allowed=Product.AxleType.values,
                    )
                    if choice is None:
                        raise ValueError("Select a supported axle type.")
                    normalized[key] = choice
                elif key == "brake":
                    choice = normalize_choice_value(
                        rendered,
                        aliases=BRAKE_ALIASES,
                        allowed=Product.BrakeType.values,
                    )
                    if choice is None:
                        raise ValueError("Select a supported brake type.")
                    normalized[key] = choice
                elif key == "tube_shape":
                    choice = normalize_choice_value(
                        rendered,
                        aliases=TUBE_SHAPE_ALIASES,
                        allowed=AxleSpec.TubeShape.values,
                    )
                    if choice is None:
                        raise ValueError("Select round, square or other.")
                    normalized[key] = choice
                elif key == "finish":
                    choice = normalize_choice_value(
                        rendered,
                        aliases=SURFACE_FINISH_ALIASES,
                        allowed=Product.SurfaceFinish.values,
                    )
                    if choice is None:
                        raise ValueError("Select a supported surface finish.")
                    normalized[key] = choice
                elif key in {"oem_requirement", "odm_requirement"}:
                    choice = normalize_choice_value(
                        rendered,
                        aliases={},
                        allowed=RFQ_REQUIREMENT_CHOICES,
                    )
                    if choice is None:
                        raise ValueError("Select yes, no or discuss.")
                    normalized[key] = choice
                else:
                    normalized[key] = rendered
            except ValueError as exc:
                errors[key] = str(exc)

        declared_dimension_unit = normalized.get("dimension_unit", "")
        if declared_dimension_unit:
            for key, explicit_unit in explicit_dimension_units.items():
                if explicit_unit != declared_dimension_unit:
                    errors[key] = (
                        "The value unit conflicts with the submitted dimension_unit."
                    )
        if errors:
            raise serializers.ValidationError(errors)
        return normalized

    def validate_items(self, value: object) -> list[dict]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise serializers.ValidationError("Items must be a list of product entries.")
        if len(value) > 20:
            raise serializers.ValidationError("No more than 20 products can be included in one RFQ.")
        validated_items: list[dict] = []
        for item in value:
            product_id = parse_strict_positive_integer(
                item.get("product_id"),
                label="Product ID",
                maximum=MAX_BIG_AUTO_FIELD_VALUE,
            )
            quantity = parse_strict_positive_integer(
                item.get("quantity", 1),
                label="Product quantity",
                maximum=100_000,
            )
            if not Product.objects.filter(
                pk=product_id,
                status=Product.Status.PUBLISHED,
                category__is_active=True,
            ).exists():
                raise serializers.ValidationError("One or more selected products are no longer available.")
            selected_options = item.get("selected_options", {})
            requested_specs = item.get("requested_specs", {})
            if not isinstance(selected_options, dict) or not isinstance(requested_specs, dict):
                raise serializers.ValidationError(
                    "Selected options and requested specifications must be objects."
                )
            encode_safe_public_json(
                selected_options,
                label="Selected options",
                maximum_length=4_000,
            )
            encode_safe_public_json(
                requested_specs,
                label="Requested specifications",
                maximum_length=4_000,
            )
            validated_items.append(
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "selected_options": selected_options,
                    "requested_specs": requested_specs,
                }
            )
        return validated_items

    def validate(self, attrs: dict) -> dict:
        attrs = super().validate(attrs)
        if attrs.pop("website", ""):
            raise serializers.ValidationError("Unable to process this request.")
        if not attrs.get("privacy_consent"):
            raise serializers.ValidationError({"privacy_consent": "Privacy consent is required."})
        turnstile_token = attrs.pop("turnstile_token", "")
        try:
            verify_turnstile_token(
                turnstile_token,
                self.context["request"],
                expected_action="rfq",
            )
        except TurnstileVerificationError as exc:
            raise serializers.ValidationError({"turnstile_token": str(exc)}) from exc
        files = self.context["request"].FILES.getlist("attachments")
        if len(files) > settings.MAX_UPLOAD_FILES:
            raise serializers.ValidationError({"attachments": f"Upload no more than {settings.MAX_UPLOAD_FILES} files."})
        if sum(uploaded_file.size for uploaded_file in files) > settings.MAX_TOTAL_UPLOAD_BYTES:
            raise serializers.ValidationError({"attachments": "Combined attachments must not exceed 25 MB."})
        for uploaded_file in files:
            if uploaded_file.size > settings.MAX_UPLOAD_BYTES:
                raise serializers.ValidationError({"attachments": f"{uploaded_file.name} exceeds the 10 MB per-file limit."})
            try:
                validate_attachment_content(
                    uploaded_file,
                    uploaded_file.name,
                    uploaded_file.content_type or "",
                )
            except AttachmentSecurityError as exc:
                raise serializers.ValidationError(
                    {"attachments": f"{uploaded_file.name}: {exc}"}
                ) from exc
        specifications = attrs.get("specifications", {})
        try:
            encode_safe_public_json(
                specifications,
                label="Technical specifications",
                maximum_length=10_000,
            )
        except serializers.ValidationError as exc:
            raise serializers.ValidationError(
                {"specifications": exc.detail}
            ) from exc
        return attrs

    def create(self, validated_data: dict) -> Inquiry:
        items = validated_data.pop("items", [])
        session_key = validated_data.pop("session_id", "")
        request = self.context["request"]
        session, attribution = build_submission_attribution(
            session_key,
            validated_data,
            request,
        )
        validated_data.pop("referrer_url", None)
        validated_data.update(attribution)
        inquiry = Inquiry.objects.create(**validated_data)
        for item in items:
            product = Product.objects.filter(
                pk=item["product_id"],
                status=Product.Status.PUBLISHED,
                category__is_active=True,
            ).first()
            if product is None:
                raise serializers.ValidationError(
                    "One or more selected products are no longer available."
                )
            snapshot = {
                "model": product.model,
                "title": product.title,
                "sku": product.sku,
                "rated_capacity": product.rated_capacity,
                "capacity_unit": product.capacity_unit,
                "dimension_unit": product.dimension_unit,
                "brake_type": product.brake_type,
                "bolt_pattern": product.bolt_pattern,
            }
            InquiryItem.objects.create(
                inquiry=inquiry,
                product=product,
                product_snapshot=snapshot,
                quantity=item["quantity"],
                selected_options=item["selected_options"],
                requested_specs=item["requested_specs"],
            )
        for uploaded_file in request.FILES.getlist("attachments"):
            attachment = InquiryAttachment.objects.create(
                inquiry=inquiry,
                file=uploaded_file,
                original_name=sanitize_upload_filename(uploaded_file.name),
                size=uploaded_file.size,
                declared_mime_type=(uploaded_file.content_type or "")[:120],
            )
            schedule_inquiry_attachment_scan(attachment)
        AnalyticsEvent.objects.create(
            event_name="submit_inquiry",
            path=inquiry.source_url[:500],
            session_id=session.session_key if session else "",
            visit_session=session,
            product=inquiry.items.first().product if inquiry.items.exists() else None,
            metadata={
                "reference": inquiry.reference,
                "server_side": True,
            },
        )
        return inquiry


class ContactMessageSerializer(serializers.ModelSerializer):
    website = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    session_id = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=100,
        default="",
    )
    turnstile_token = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=2048,
        default="",
    )
    referrer_url = serializers.URLField(
        write_only=True,
        required=False,
        allow_blank=True,
        max_length=1000,
        default="",
    )

    class Meta:
        model = ContactMessage
        fields = (
            "reference", "name", "email", "phone", "company", "country",
            "subject", "message",
            "privacy_consent", "website", "session_id", "turnstile_token",
            "source_url", "referrer_url", "first_touch_source", "last_touch_source", "utm_source",
            "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "first_landing_page", "last_landing_page", "created_at",
        )
        read_only_fields = ("reference", "created_at")
        extra_kwargs = {
            "message": {"max_length": 10_000},
            "source_url": {"write_only": True},
            "first_touch_source": {"write_only": True},
            "last_touch_source": {"write_only": True},
            "utm_source": {"write_only": True},
            "utm_medium": {"write_only": True},
            "utm_campaign": {"write_only": True},
            "utm_term": {"write_only": True},
            "utm_content": {"write_only": True},
            "first_landing_page": {"write_only": True},
            "last_landing_page": {"write_only": True},
        }

    def validate_session_id(self, value: str) -> str:
        return validate_session_key(value) if value else ""

    def validate_phone(self, value: str) -> str:
        return validate_phone_number(value)

    def validate(self, attrs: dict) -> dict:
        if attrs.pop("website", ""):
            raise serializers.ValidationError("Unable to process this request.")
        if not attrs.get("privacy_consent"):
            raise serializers.ValidationError({"privacy_consent": "Privacy consent is required."})
        turnstile_token = attrs.pop("turnstile_token", "")
        try:
            verify_turnstile_token(
                turnstile_token,
                self.context["request"],
                expected_action="contact",
            )
        except TurnstileVerificationError as exc:
            raise serializers.ValidationError({"turnstile_token": str(exc)}) from exc
        return attrs

    def create(self, validated_data: dict) -> ContactMessage:
        session_key = validated_data.pop("session_id", "")
        session, attribution = build_submission_attribution(
            session_key,
            validated_data,
            self.context["request"],
        )
        validated_data.pop("referrer_url", None)
        validated_data.update(attribution)
        message = ContactMessage.objects.create(**validated_data)
        AnalyticsEvent.objects.create(
            event_name="submit_contact",
            path=message.source_url[:500],
            session_id=session.session_key if session else "",
            visit_session=session,
            metadata={
                "reference": message.reference,
                "server_side": True,
            },
        )
        return message


class AnalyticsEventSerializer(serializers.ModelSerializer):
    product_id = serializers.PrimaryKeyRelatedField(
        pk_field=serializers.IntegerField(min_value=1, max_value=MAX_BIG_AUTO_FIELD_VALUE),
        source="product",
        queryset=Product.objects.filter(
            status=Product.Status.PUBLISHED,
            category__is_active=True,
        ),
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = AnalyticsEvent
        fields = ("event_name", "path", "session_id", "product_id", "metadata")

    def validate_event_name(self, value: str) -> str:
        aliases = {
            "product_view": "view_product",
            "product_search": "search_product",
            "product_filter": "use_product_filter",
            "rfq_add": "add_to_inquiry_list",
            "rfq_remove": "remove_from_inquiry_list",
            "rfq_start": "start_inquiry",
            "rfq_submit": "submit_inquiry",
            "contact_submit": "submit_contact",
            "download": "download_document",
            "whatsapp_click": "click_whatsapp",
            "email_click": "click_email",
            "phone_click": "click_phone",
        }
        value = aliases.get(value, value)
        if value in {"submit_inquiry", "submit_contact"}:
            raise serializers.ValidationError(
                "Submission events are recorded by the server."
            )
        permitted = {
            "page_view",
            "view_product",
            "search_product",
            "use_product_filter",
            "add_to_inquiry_list",
            "remove_from_inquiry_list",
            "download_document",
            "start_inquiry",
            "click_whatsapp",
            "click_email",
            "click_phone",
        }
        if value not in permitted:
            raise serializers.ValidationError("Unsupported event.")
        return value

    def validate_metadata(self, value: object) -> dict:
        if not isinstance(value, dict):
            raise serializers.ValidationError("Event metadata must be an object.")
        # At most one server_side member is removed below. Bound the original
        # object before making a defensive copy of attacker-supplied metadata.
        if len(value) > MAX_PUBLIC_JSON_NODES:
            raise serializers.ValidationError("Event metadata has too many values.")
        value = dict(value)
        value.pop("server_side", None)
        encode_safe_public_json(
            value,
            label="Event metadata",
            maximum_length=4_000,
        )
        return value

    def validate_session_id(self, value: str) -> str:
        return validate_session_key(value) if value else ""

    def create(self, validated_data: dict) -> AnalyticsEvent:
        session_key = validated_data.get("session_id", "")
        if session_key:
            validated_data["visit_session"] = upsert_visit_session(
                session_key,
                {},
                self.context["request"],
            )
        if "product" not in validated_data:
            metadata_product_id = validated_data.get("metadata", {}).get("product_id")
            try:
                metadata_product_id = parse_strict_positive_integer(
                    metadata_product_id,
                    label="Product ID",
                    maximum=MAX_BIG_AUTO_FIELD_VALUE,
                )
            except serializers.ValidationError:
                metadata_product_id = None
            if metadata_product_id:
                validated_data["product"] = Product.objects.filter(
                    pk=metadata_product_id,
                    status=Product.Status.PUBLISHED,
                    category__is_active=True,
                ).first()
        return AnalyticsEvent.objects.create(**validated_data)

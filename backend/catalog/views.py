from __future__ import annotations

import logging
import math
import re
from decimal import Decimal

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.core.cache import cache
from django.core.validators import validate_slug
from django.db import IntegrityError, transaction
from django.db import connection
from django.db.models import (
    Case,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Prefetch,
    Q,
    Value,
    When,
)
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import generics, status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from .models import (
    Application,
    AxleSpec,
    Banner,
    Category,
    ContactMessage,
    CompanyProfile,
    Inquiry,
    InquiryAttachment,
    Product,
    ProductDocument,
    ProductImage,
    NewsArticle,
    SiteSetting,
)
from .file_security import validate_product_document_upload
from .serializers import (
    AnalyticsEventSerializer, ApplicationSerializer, BannerSerializer,
    CategorySerializer, ContactMessageSerializer, InquirySerializer,
    MAX_BIG_AUTO_FIELD_VALUE,
    PageViewSerializer, ProductDetailSerializer, ProductDocumentSerializer,
    ProductListSerializer, NewsArticleDetailSerializer,
    NewsArticleListSerializer, SiteSettingSerializer,
    CompanyProfileSerializer,
    VisitSessionSerializer,
)
from .specification_rules import (
    BRAKE_ALIASES as BRAKE_FILTER_ALIASES,
    SURFACE_FINISH_ALIASES as SURFACE_FINISH_FILTER_ALIASES,
    TUBE_SHAPE_ALIASES as TUBE_SHAPE_FILTER_ALIASES,
    normalize_choice_value,
    parse_dimension_value,
)
from .tasks import (
    enqueue_contact_notification,
    enqueue_inquiry_notification,
    send_contact_notification,
    send_inquiry_notification,
)
from .validators import secure_public_url_or_blank
from .submissions import SubmissionConflict, submission_key_hash, submission_payload_hash
from .throttles import (
    AnalyticsEventThrottle, AttachmentUploadIpThrottle, AttributionThrottle,
    DocumentDownloadThrottle,
    EmailRateThrottle,
    ContactEmailThrottle, ContactIpThrottle,
    RFQEmailThrottle, RFQIpThrottle,
)

logger = logging.getLogger(__name__)

APPLICATION_FILTER_ALIASES = {
    "utility-trailer": "utility-trailers",
    "boat-trailer": "boat-trailers",
    "agricultural-trailer": "agricultural-trailers",
    "car-hauler": "car-haulers",
    "construction-trailer": "construction-trailers",
    "heavy-duty-trailer": "heavy-duty-transport",
    "rv": "rv-caravan",
}

BOLT_PATTERN_FILTER_PATTERN = re.compile(
    r"^(?P<stud_count>[1-9]\d?)\s*(?:x|on)\s*"
    r"(?P<diameter>\d{1,3}(?:\.\d{1,3})?)$",
    re.IGNORECASE,
)
LUG_COUNT_FILTER_PATTERN = re.compile(
    r"^(?P<lug_count>[4-9])\s*lug(?:s)?$",
    re.IGNORECASE,
)
PRODUCT_ORDERING_FIELDS = {
    "capacity_asc": "rated_capacity_lb",
    "capacity_desc": "-rated_capacity_lb",
    "model": "model",
    "newest": "-created_at",
    "published_at": "created_at",
    "-published_at": "-created_at",
    "updated_at": "updated_at",
    "-updated_at": "-updated_at",
}
MAX_PRODUCT_PAGE = 10_000


def normalize_choice_filter(
    value: str,
    *,
    aliases: dict[str, str],
    allowed: list[str],
    field_name: str,
    max_length: int,
) -> str:
    if len(value) > max_length:
        raise ValidationError(
            {field_name: f"{field_name.replace('_', ' ').title()} filter is too long."}
        )
    normalized = normalize_choice_value(
        value,
        aliases=aliases,
        allowed=allowed,
    )
    if normalized is None:
        raise ValidationError({field_name: f"Unsupported {field_name.replace('_', ' ')}."})
    return normalized


def validate_slug_filter(value: str, *, field_name: str, max_length: int) -> str:
    if len(value) > max_length:
        raise ValidationError(
            {field_name: f"{field_name.replace('_', ' ').title()} filter is too long."}
        )
    try:
        validate_slug(value)
    except DjangoValidationError as exc:
        raise ValidationError(
            {field_name: "Use a valid URL slug containing letters, numbers, hyphens or underscores."}
        ) from exc
    return value


def validate_boolean_filter(value: str, field_name: str) -> bool:
    if len(value) > 5 or value.lower() not in {"true", "false"}:
        raise ValidationError({field_name: "Use true or false."})
    return value.lower() == "true"


def parse_capacity_filter(value: str, field_name: str) -> float:
    if len(value) > 32:
        raise ValidationError({field_name: "Capacity filter is too long."})
    try:
        number = float(value)
    except ValueError as exc:
        raise ValidationError({field_name: "Capacity must be a number."}) from exc
    if not math.isfinite(number) or number < 0:
        raise ValidationError(
            {field_name: "Capacity must be finite and cannot be negative."}
        )
    return number


def normalize_bolt_pattern_filter(value: str) -> tuple[str, int | None]:
    max_length = Product._meta.get_field("bolt_pattern").max_length
    if len(value) > max_length:
        raise ValidationError({"bolt_pattern": "Bolt pattern filter is too long."})
    candidate = value.strip()
    lug_match = LUG_COUNT_FILTER_PATTERN.fullmatch(candidate)
    if lug_match is not None:
        lug_count = int(lug_match.group("lug_count"))
        return f"{lug_count} lug", lug_count

    match = BOLT_PATTERN_FILTER_PATTERN.fullmatch(candidate)
    if match is None or Decimal(match.group("diameter")) <= 0:
        raise ValidationError(
            {
                "bolt_pattern": (
                    "Use a lug count such as '4 lug', or a complete pattern "
                    "such as '5 on 4.5' or '5x4.5'."
                )
            }
        )
    return f"{match.group('stud_count')} on {match.group('diameter')}", None


def is_safe_bolt_pattern_option(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= Product._meta.get_field("bolt_pattern").max_length
        and value.isprintable()
    )


def bolt_pattern_matches(product_pattern: str, requested_pattern: str) -> bool:
    if not requested_pattern:
        return False
    normalized, lug_count = normalize_bolt_pattern_filter(requested_pattern)
    if lug_count is not None:
        candidate = product_pattern.strip().casefold()
        return candidate.startswith(f"{lug_count} ") or candidate.startswith(
            f"{lug_count}x"
        )
    return product_pattern.strip().casefold() == normalized.casefold()


def parse_dimension_filter(value: str, field_name: str) -> tuple[Decimal, str | None]:
    if len(value) > 60:
        raise ValidationError({field_name: f"{field_name.replace('_', ' ').title()} filter is too long."})
    try:
        return parse_dimension_value(value)
    except ValueError as exc:
        raise ValidationError({field_name: str(exc)}) from exc


def dimension_in_inches(value: Decimal, unit: str) -> float:
    if unit == Product.DimensionUnit.MM:
        return float(value / Decimal("25.4"))
    return float(value)


def normalized_dimension_annotation(field_name: str) -> Case:
    return Case(
        When(
            dimension_unit=Product.DimensionUnit.MM,
            then=ExpressionWrapper(
                F(field_name) / Value(25.4),
                output_field=FloatField(),
            ),
        ),
        default=F(field_name),
        output_field=FloatField(),
    )


def active_applications_prefetch() -> Prefetch:
    return Prefetch(
        "applications",
        queryset=Application.objects.filter(is_active=True),
        to_attr="active_applications",
    )


def card_images_prefetch() -> Prefetch:
    return Prefetch(
        "images",
        queryset=ProductImage.objects.order_by("sort_order", "id"),
        to_attr="card_images",
    )


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        return Category.objects.filter(is_active=True).annotate(product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED)))


class CategoryDetailView(generics.RetrieveAPIView):
    serializer_class = CategorySerializer
    lookup_field = "slug"

    def get_queryset(self):
        return Category.objects.filter(is_active=True).annotate(
            product_count=Count(
                "products",
                filter=Q(products__status=Product.Status.PUBLISHED),
            )
        )


class ApplicationListView(generics.ListAPIView):
    serializer_class = ApplicationSerializer
    pagination_class = None
    queryset = Application.objects.filter(is_active=True)


class ApplicationDetailView(generics.RetrieveAPIView):
    serializer_class = ApplicationSerializer
    lookup_field = "slug"
    queryset = Application.objects.filter(is_active=True)


class ProductListView(generics.ListAPIView):
    serializer_class = ProductListSerializer

    def get_queryset(self):
        params = self.request.query_params
        if "page" in params:
            raw_page = params.get("page", "")
            invalid_numbered_page = (
                raw_page != "last"
                and (
                    len(raw_page) > 5
                    or re.fullmatch(r"[1-9][0-9]*", raw_page) is None
                    or int(raw_page) > MAX_PRODUCT_PAGE
                )
            )
            if invalid_numbered_page:
                raise ValidationError(
                    {
                        "page": (
                            "Page must be a positive integer no greater than "
                            f"{MAX_PRODUCT_PAGE}."
                        )
                    }
                )
        queryset = Product.objects.filter(
            status=Product.Status.PUBLISHED,
            category__is_active=True,
        ).annotate(
            rated_capacity_lb=Case(
                When(
                    capacity_unit=Product.CapacityUnit.KG,
                    then=F("rated_capacity") * Value(2.2046226218),
                ),
                default=F("rated_capacity"),
                output_field=FloatField(),
            ),
            hub_face_inches=normalized_dimension_annotation("hub_face_length"),
            spring_center_inches=normalized_dimension_annotation("spring_center"),
        ).select_related("category").prefetch_related(
            active_applications_prefetch(),
            card_images_prefetch(),
        )
        if category := params.get("category"):
            category = validate_slug_filter(
                category,
                field_name="category",
                max_length=Category._meta.get_field("slug").max_length,
            )
            queryset = queryset.filter(category__slug=category)
        if raw_query := params.get("q"):
            if len(raw_query) > 120:
                raise ValidationError({"q": "Search query must be at most 120 characters."})
            query = raw_query.strip()
            if query and not query.isprintable():
                raise ValidationError({"q": "Search query contains unsupported characters."})
            if query:
                queryset = queryset.filter(
                    Q(title__icontains=query)
                    | Q(model__icontains=query)
                    | Q(sku__icontains=query)
                    | Q(short_description__icontains=query)
                    | Q(category__name__icontains=query)
                    | Q(brake_type__icontains=query)
                    | Q(bolt_pattern__icontains=query)
                    | Q(surface_finish__icontains=query)
                    | (
                        Q(applications__is_active=True)
                        & (
                            Q(applications__name__icontains=query)
                            | Q(applications__slug__icontains=query)
                        )
                    )
                )

        axle_filters = {}
        axle_type_max_length = Product._meta.get_field("axle_type").max_length
        for field_name in ("axle_type", "type"):
            if value := params.get(field_name):
                if (
                    len(value) > axle_type_max_length
                    or value not in Product.AxleType.values
                ):
                    raise ValidationError({field_name: "Unsupported axle type."})
                axle_filters[field_name] = value
        if len(set(axle_filters.values())) > 1:
            raise ValidationError(
                {"axle_type": "axle_type and type filters must not conflict."}
            )
        if axle_type := (
            axle_filters.get("axle_type") or axle_filters.get("type")
        ):
            queryset = queryset.filter(axle_type=axle_type)

        brake_filters = {}
        for field_name in ("brake_type", "brake"):
            if value := params.get(field_name):
                brake_filters[field_name] = normalize_choice_filter(
                    value,
                    aliases=BRAKE_FILTER_ALIASES,
                    allowed=Product.BrakeType.values,
                    field_name=field_name,
                    max_length=Product._meta.get_field("brake_type").max_length,
                )
        if len(set(brake_filters.values())) > 1:
            raise ValidationError(
                {"brake_type": "brake_type and brake filters must not conflict."}
            )
        if brake_type := (
            brake_filters.get("brake_type") or brake_filters.get("brake")
        ):
            queryset = queryset.filter(brake_type__iexact=brake_type)
        if raw_bolt_pattern := params.get("bolt_pattern"):
            try:
                bolt_pattern, lug_count = normalize_bolt_pattern_filter(
                    raw_bolt_pattern
                )
            except ValidationError:
                is_published_option = (
                    is_safe_bolt_pattern_option(raw_bolt_pattern)
                    and Product.objects.filter(
                        status=Product.Status.PUBLISHED,
                        category__is_active=True,
                        bolt_pattern__iexact=raw_bolt_pattern,
                    ).exists()
                )
                if not is_published_option:
                    raise
                queryset = queryset.filter(
                    bolt_pattern__iexact=raw_bolt_pattern
                )
            else:
                if lug_count is not None:
                    queryset = queryset.filter(
                        Q(bolt_pattern__istartswith=f"{lug_count} ")
                        | Q(bolt_pattern__istartswith=f"{lug_count}x")
                    )
                else:
                    queryset = queryset.filter(
                        Q(bolt_pattern__iexact=raw_bolt_pattern)
                        | Q(bolt_pattern__iexact=bolt_pattern)
                    )
        if finish := params.get("finish"):
            finish = normalize_choice_filter(
                finish,
                aliases=SURFACE_FINISH_FILTER_ALIASES,
                allowed=Product.SurfaceFinish.values,
                field_name="finish",
                max_length=Product._meta.get_field("surface_finish").max_length,
            )
            queryset = queryset.filter(surface_finish=finish)
        dimension_unit = params.get("dimension_unit")
        if dimension_unit and (
            len(dimension_unit) > Product._meta.get_field("dimension_unit").max_length
            or dimension_unit not in Product.DimensionUnit.values
        ):
            raise ValidationError(
                {"dimension_unit": "Dimension unit must be in or mm."}
            )
        hub_face = params.get("hub_face")
        spring_center = params.get("spring_center")
        for parameter, annotation, raw_value in (
            ("hub_face", "hub_face_inches", hub_face),
            ("spring_center", "spring_center_inches", spring_center),
        ):
            if not raw_value:
                continue
            requested, explicit_unit = parse_dimension_filter(raw_value, parameter)
            if explicit_unit and dimension_unit and explicit_unit != dimension_unit:
                raise ValidationError(
                    {parameter: "The value unit conflicts with dimension_unit."}
                )
            requested_inches = dimension_in_inches(
                requested,
                explicit_unit or dimension_unit or Product.DimensionUnit.IN,
            )
            tolerance = max(abs(requested_inches) * 1e-9, 0.005)
            queryset = queryset.filter(
                **{
                    f"{annotation}__gte": requested_inches - tolerance,
                    f"{annotation}__lte": requested_inches + tolerance,
                }
            )
        if dimension_unit and not hub_face and not spring_center:
            queryset = queryset.filter(dimension_unit=dimension_unit)
        if tube_shape := params.get("tube_shape"):
            tube_shape = normalize_choice_filter(
                tube_shape,
                aliases=TUBE_SHAPE_FILTER_ALIASES,
                allowed=AxleSpec.TubeShape.values,
                field_name="tube_shape",
                max_length=AxleSpec._meta.get_field("tube_shape").max_length,
            )
            queryset = queryset.filter(axle_spec__tube_shape=tube_shape)
        if customizable := params.get("customizable"):
            queryset = queryset.filter(
                is_customizable=validate_boolean_filter(customizable, "customizable")
            )
        if made_to_order := params.get("made_to_order"):
            queryset = queryset.filter(
                made_to_order=validate_boolean_filter(made_to_order, "made_to_order")
            )
        if oem := params.get("oem"):
            queryset = queryset.filter(
                oem_available=validate_boolean_filter(oem, "oem")
            )
        capacity_unit = params.get("capacity_unit")
        capacity_min = params.get("capacity_min")
        capacity_max = params.get("capacity_max")
        if capacity_unit:
            if (
                len(capacity_unit) > Product._meta.get_field("capacity_unit").max_length
                or capacity_unit not in Product.CapacityUnit.values
            ):
                raise ValidationError(
                    {"capacity_unit": "Capacity unit must be lb or kg."}
                )
            if not capacity_min and not capacity_max:
                queryset = queryset.filter(capacity_unit=capacity_unit)
        if capacity := params.get("capacity"):
            threshold = parse_capacity_filter(capacity, "capacity")
            queryset = queryset.filter(
                capacity_unit=capacity_unit or Product.CapacityUnit.LB,
                rated_capacity__gte=threshold,
            )
        if capacity_min or capacity_max:
            minimum = (
                parse_capacity_filter(capacity_min, "capacity")
                if capacity_min
                else None
            )
            maximum = (
                parse_capacity_filter(capacity_max, "capacity")
                if capacity_max
                else None
            )
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValidationError({"capacity": "Minimum capacity cannot exceed maximum capacity."})
            conversion = 2.2046226218 if capacity_unit == Product.CapacityUnit.KG else 1
            if minimum is not None:
                queryset = queryset.filter(rated_capacity_lb__gte=minimum * conversion)
            if maximum is not None:
                queryset = queryset.filter(rated_capacity_lb__lte=maximum * conversion)
        if application := params.get("application"):
            application = validate_slug_filter(
                application,
                field_name="application",
                max_length=Application._meta.get_field("slug").max_length,
            )
            application = APPLICATION_FILTER_ALIASES.get(application, application)
            queryset = queryset.filter(
                applications__slug=application,
                applications__is_active=True,
            )
        ordering_filters = {}
        for field_name in ("ordering", "sort"):
            if value := params.get(field_name):
                if len(value) > 30 or value not in PRODUCT_ORDERING_FIELDS:
                    raise ValidationError({field_name: "Unsupported product ordering."})
                ordering_filters[field_name] = value
        if len(set(ordering_filters.values())) > 1:
            raise ValidationError(
                {"ordering": "ordering and sort filters must not conflict."}
            )
        requested_ordering = (
            ordering_filters.get("ordering") or ordering_filters.get("sort")
        )
        ordering = PRODUCT_ORDERING_FIELDS.get(requested_ordering, "position")
        return queryset.order_by(ordering).distinct()


class ProductDetailView(generics.RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return Product.objects.filter(
            status=Product.Status.PUBLISHED,
            category__is_active=True,
        ).select_related("category", "axle_spec").prefetch_related(
            active_applications_prefetch(),
            "images",
            Prefetch(
                "documents",
                queryset=ProductDocument.objects.filter(is_active=True),
                to_attr="active_documents",
            ),
        )


class ProductRelatedView(generics.GenericAPIView):
    serializer_class = ProductListSerializer

    def get(self, request, slug: str):
        product = get_object_or_404(
            Product,
            slug=slug,
            status=Product.Status.PUBLISHED,
            category__is_active=True,
        )
        related = (
            Product.objects.filter(
                status=Product.Status.PUBLISHED,
                category__is_active=True,
                category=product.category,
            )
            .exclude(pk=product.pk)
            .select_related("category")
            .prefetch_related(
                active_applications_prefetch(),
                card_images_prefetch(),
            )
            .order_by("position", "title")[:3]
        )
        return Response(
            self.get_serializer(related, many=True).data,
        )


class NewsArticleListView(generics.ListAPIView):
    serializer_class = NewsArticleListSerializer

    class NewsPagination(PageNumberPagination):
        page_size_query_param = "page_size"
        max_page_size = 12

    pagination_class = NewsPagination

    def get_queryset(self):
        articles = NewsArticle.objects.filter(
            status=NewsArticle.Status.PUBLISHED,
            published_at__lte=timezone.now(),
        )
        featured_first = self.request.query_params.get("featured_first", "false")
        if featured_first not in {"true", "false"}:
            raise ValidationError({"featured_first": "Use true or false."})
        if featured_first == "true":
            return articles.order_by("-is_featured", "-published_at", "-created_at")
        return articles


class NewsArticleDetailView(generics.RetrieveAPIView):
    serializer_class = NewsArticleDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return NewsArticle.objects.filter(
            status=NewsArticle.Status.PUBLISHED,
            published_at__lte=timezone.now(),
        )


class BannerListView(generics.ListAPIView):
    serializer_class = BannerSerializer
    pagination_class = None

    def get_queryset(self):
        now = timezone.now()
        return Banner.objects.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=now),
            Q(ends_at__isnull=True) | Q(ends_at__gte=now),
        )


@api_view(["GET"])
def site_settings_view(request):
    instance = SiteSetting.objects.first()
    if instance is None:
        return Response({})
    return Response(
        SiteSettingSerializer(instance, context={"request": request}).data
    )


@api_view(["GET"])
def company_profile_view(request):
    profile = CompanyProfile.objects.filter(is_published=True).prefetch_related("photos").first()
    if profile is None:
        return Response({})
    return Response(CompanyProfileSerializer(profile, context={"request": request}).data)


class PublicFormCreateView(generics.CreateAPIView):
    email_throttle_class: type[EmailRateThrottle]

    def submission_save_kwargs(self):
        if self._submission_key_hash is None:
            return {}
        return {
            "submission_key_hash": self._submission_key_hash,
            "submission_payload_hash": self._submission_payload_hash,
        }

    def replay_submission(self, instance):
        if not constant_time_compare(instance.submission_payload_hash, self._submission_payload_hash):
            raise SubmissionConflict()
        return Response({"reference": instance.reference}, status=status.HTTP_201_CREATED)

    def create(self, request, *args, **kwargs):
        self._submission_key_hash = submission_key_hash(request, self.submission_kind)
        self._submission_payload_hash = ""
        if self._submission_key_hash is None:
            return super().create(request, *args, **kwargs)
        self._submission_payload_hash = submission_payload_hash(request)
        existing = self.submission_model.objects.filter(submission_key_hash=self._submission_key_hash).first()
        if existing is not None:
            return self.replay_submission(existing)
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            # The unique database column also serializes concurrent requests.
            # The losing transaction has rolled back, including its callbacks.
            existing = self.submission_model.objects.filter(submission_key_hash=self._submission_key_hash).first()
            if existing is None:
                raise
            return self.replay_submission(existing)

    def check_throttles(self, request):
        # DRF normally evaluates every throttle even after one refuses. Stop
        # at the first attempt limit so blocked IPs cannot trigger multipart
        # parsing or consume another bucket through subsequent throttles.
        for throttle in self.get_throttles():
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())

    def check_email_throttle(self, serializer):
        # perform_create is reached only after all field, CAPTCHA and form
        # validation succeeds. Unverified payloads must not spend an arbitrary
        # customer's global email quota. This does not prove email ownership.
        throttle = self.email_throttle_class(email=serializer.validated_data["email"])
        if not throttle.allow_request(self.request, self):
            self.throttled(self.request, throttle.wait())


class InquiryCreateView(PublicFormCreateView):
    serializer_class = InquirySerializer
    submission_kind = "inquiry"
    submission_model = Inquiry
    email_throttle_class = RFQEmailThrottle
    throttle_classes = [
        RFQIpThrottle,
        AttachmentUploadIpThrottle,
    ]

    def perform_create(self, serializer):
        self.check_email_throttle(serializer)
        with transaction.atomic():
            inquiry = serializer.save(**self.submission_save_kwargs())
            transaction.on_commit(lambda: self._queue_notification(inquiry.pk))

    @staticmethod
    def _queue_notification(inquiry_id: int) -> None:
        enqueue_inquiry_notification(inquiry_id)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return Response({"reference": response.data["reference"], "message": "Your RFQ has been received."}, status=status.HTTP_201_CREATED)


class ContactCreateView(PublicFormCreateView):
    serializer_class = ContactMessageSerializer
    submission_kind = "contact"
    submission_model = ContactMessage
    email_throttle_class = ContactEmailThrottle
    throttle_classes = [ContactIpThrottle]

    def perform_create(self, serializer):
        self.check_email_throttle(serializer)
        with transaction.atomic():
            message = serializer.save(**self.submission_save_kwargs())
            transaction.on_commit(lambda: self._queue_notification(message.pk))

    @staticmethod
    def _queue_notification(message_id: int) -> None:
        enqueue_contact_notification(message_id)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return Response(
            {
                "reference": response.data["reference"],
                "message": "Your message has been received.",
            },
            status=status.HTTP_201_CREATED,
        )


class AnalyticsEventCreateView(generics.CreateAPIView):
    serializer_class = AnalyticsEventSerializer
    throttle_classes = [AnalyticsEventThrottle]


class VisitSessionCreateView(generics.CreateAPIView):
    serializer_class = VisitSessionSerializer
    throttle_classes = [AttributionThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            {"message": "Session activity recorded."},
            status=status.HTTP_201_CREATED,
        )


class PageViewCreateView(generics.CreateAPIView):
    serializer_class = PageViewSerializer
    throttle_classes = [AttributionThrottle]


@api_view(["GET"])
def health_view(request):
    try:
        connection.ensure_connection()
        cache.set("healthcheck", "ok", timeout=5)
        if cache.get("healthcheck") != "ok":
            raise RuntimeError("Cache health check failed")
    except Exception:
        logger.exception("Health check failed")
        return Response({"status": "unhealthy"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"status": "ok"})


@api_view(["GET"])
def home_view(request):
    categories = Category.objects.filter(is_active=True).annotate(product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED)))[:6]
    products = Product.objects.filter(
        status=Product.Status.PUBLISHED,
        featured=True,
        category__is_active=True,
    ).select_related("category").prefetch_related(
        active_applications_prefetch(),
        card_images_prefetch(),
    )[:3]
    return Response({
        "categories": CategorySerializer(categories, many=True).data,
        "featured_products": ProductListSerializer(
            products,
            many=True,
            context={"request": request},
        ).data,
        "applications": ApplicationSerializer(
            Application.objects.filter(is_active=True)[:6],
            many=True,
        ).data,
    })


@api_view(["GET"])
def navigation_view(request):
    categories = Category.objects.filter(is_active=True).annotate(
        product_count=Count(
            "products",
            filter=Q(products__status=Product.Status.PUBLISHED),
        )
    )
    return Response(
        {
            "items": [
                {"label": "Products", "url": "/products"},
                {"label": "Applications", "url": "/applications"},
                {"label": "News", "url": "/news"},
                {"label": "About us", "url": "/about"},
                {"label": "Contact", "url": "/contact"},
            ],
            "product_categories": CategorySerializer(
                categories,
                many=True,
                context={"request": request},
            ).data,
        }
    )


@api_view(["GET"])
def filter_options_view(request):
    products = Product.objects.filter(
        status=Product.Status.PUBLISHED,
        category__is_active=True,
    )
    capacities_by_unit = {
        unit: sorted(
            {
                item
                for item in products.filter(capacity_unit=unit).values_list(
                    "rated_capacity",
                    flat=True,
                )
                if item
            }
        )
        for unit in Product.CapacityUnit.values
    }
    return Response({
        "capacities": capacities_by_unit[Product.CapacityUnit.LB],
        "capacities_by_unit": capacities_by_unit,
        "capacity_units": [
            {"value": value, "label": label}
            for value, label in Product.CapacityUnit.choices
        ],
        "brake_types": sorted({item for item in products.values_list("brake_type", flat=True) if item}),
        "axle_types": [{"value": value, "label": label} for value, label in Product.AxleType.choices],
        "bolt_patterns": sorted(
            {
                item
                for item in products.values_list("bolt_pattern", flat=True)
                if is_safe_bolt_pattern_option(item)
            }
        ),
        "surface_finishes": sorted({item for item in products.values_list("surface_finish", flat=True) if item}),
        "tube_shapes": sorted(
            {
                item
                for item in products.values_list("axle_spec__tube_shape", flat=True)
                if item
            }
        ),
        "made_to_order": [
            {"value": "true", "label": "Made to order / configurable"},
            {"value": "false", "label": "Standard only"},
        ],
    })


@api_view(["GET"])
def document_list_view(request):
    documents = ProductDocument.objects.filter(
        is_active=True,
        product__status=Product.Status.PUBLISHED,
        product__category__is_active=True,
    ).select_related("product")
    if product := request.query_params.get("product"):
        if len(product) > 100:
            raise ValidationError({"product": "Product filter is too long."})
        if not product.isprintable():
            # PostgreSQL rejects NUL bytes in text parameters. SQLite accepts
            # them, so reject controls before either database sees the filter.
            raise ValidationError(
                {"product": "Product filter contains unsupported characters."}
            )
        # isdigit() also accepts characters such as superscript 2, which
        # int() cannot parse. Only ASCII decimal strings denote primary keys;
        # other bounded strings remain valid model/slug search values.
        if product.isascii() and product.isdecimal():
            product_id = int(product)
            documents = (
                documents.filter(product_id=product_id)
                if 1 <= product_id <= MAX_BIG_AUTO_FIELD_VALUE
                else documents.none()
            )
        else:
            documents = documents.filter(
                Q(product__slug=product) | Q(product__model__iexact=product)
            )
    if document_type := request.query_params.get("type"):
        if document_type not in ProductDocument.DocumentType.values:
            raise ValidationError({"type": "Unsupported document type."})
        documents = documents.filter(document_type=document_type)
    if file_format := request.query_params.get("format"):
        file_format = file_format.strip().upper()
        if (
            not file_format
            or len(file_format) > 30
            or not file_format.replace("-", "").isalnum()
        ):
            raise ValidationError({"format": "Unsupported document format."})
        # file_format is a Python property (extension of the file name / URL
        # path), not a DB column, so it cannot be matched exactly in SQL.
        # Narrow candidates in the database, then apply the property semantics
        # while streaming instead of materializing the whole queryset.
        marker = f".{file_format}"
        documents = documents.filter(
            Q(file__icontains=marker) | Q(external_url__icontains=marker)
        )
        documents = [
            document
            for document in documents.iterator()
            if document.file_format == file_format
        ]
    return Response(ProductDocumentSerializer(documents, many=True, context={"request": request}).data)


@api_view(["GET"])
@throttle_classes([DocumentDownloadThrottle])
def document_download_view(request, pk: int):
    document = get_object_or_404(
        ProductDocument.objects.select_related("product", "product__category"),
        pk=pk,
        is_active=True,
        product__status=Product.Status.PUBLISHED,
        product__category__is_active=True,
    )
    preview_requested = request.query_params.get("preview") == "1"
    if document.external_url:
        external_url = secure_public_url_or_blank(document.external_url)
        if not external_url:
            logger.error(
                "Blocked unsafe published document URL",
                extra={"document_id": document.pk},
            )
            return Response(
                {"detail": "This document file is unavailable."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if preview_requested and not document.previewable:
            return Response(
                {"detail": "This document type cannot be previewed safely."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not preview_requested:
            ProductDocument.objects.filter(pk=document.pk).update(
                downloads=F("downloads") + 1
            )
        return HttpResponseRedirect(external_url)
    if not document.file:
        return Response({"detail": "This document has no downloadable file."}, status=status.HTTP_404_NOT_FOUND)
    try:
        # ORM writes can bypass Model.full_clean(); revalidate stored bytes at
        # the only public delivery boundary so legacy/forged files fail closed.
        validate_product_document_upload(document.file)
        file_handle = document.file.open("rb")
    except (DjangoValidationError, OSError, ValueError):
        try:
            document.file.close()
        except OSError:
            pass
        logger.exception(
            "Published product document failed delivery validation",
            extra={"document_id": document.pk},
        )
        return Response({"detail": "This document file is unavailable."}, status=status.HTTP_404_NOT_FOUND)
    filename = document.file.name.rsplit("/", 1)[-1]
    preview = preview_requested and document.previewable
    if not preview:
        ProductDocument.objects.filter(pk=document.pk).update(
            downloads=F("downloads") + 1
        )
    return FileResponse(file_handle, as_attachment=not preview, filename=filename)


@staff_member_required
def inquiry_attachment_download_view(request, pk: int):
    if not (
        request.user.has_perm("catalog.view_inquiryattachment")
        or request.user.has_perm("catalog.change_inquiry")
    ):
        raise PermissionDenied
    attachment = get_object_or_404(InquiryAttachment, pk=pk)
    if attachment.scan_status != InquiryAttachment.ScanStatus.CLEAN:
        return HttpResponse(
            "This attachment is quarantined until malware scanning reports it clean.",
            status=423,
            content_type="text/plain; charset=utf-8",
        )
    try:
        file_handle = attachment.file.open("rb")
    except OSError:
        logger.exception("Private inquiry attachment is missing", extra={"attachment_id": attachment.pk})
        return HttpResponse(
            "This attachment file is unavailable.",
            status=404,
            content_type="text/plain; charset=utf-8",
        )
    LogEntry.objects.log_actions(
        request.user.pk,
        InquiryAttachment.objects.filter(pk=attachment.pk),
        CHANGE,
        "Downloaded private inquiry attachment.",
        single_object=True,
    )
    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=attachment.original_name,
    )

from __future__ import annotations

import logging
import re

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.core.cache import cache
from django.core.validators import validate_slug
from django.db import IntegrityError, transaction
from django.db import connection
from django.db.models import Count, F, Prefetch, Q
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
    FAQ,
    Application,
    Banner,
    Category,
    ContactMessage,
    CompanyProfile,
    Inquiry,
    InquiryAttachment,
    Product,
    ProductDocument,
    ProductImage,
    ProductSpecValue,
    NewsArticle,
    SiteSetting,
)
from .file_security import validate_product_document_upload
from .serializers import (
    AnalyticsEventSerializer, ApplicationSerializer, BannerSerializer,
    CategorySerializer, ContactMessageSerializer, FAQSerializer, InquirySerializer,
    MAX_BIG_AUTO_FIELD_VALUE,
    PageViewSerializer, ProductDetailSerializer, ProductDocumentSerializer,
    ProductListSerializer, NewsArticleDetailSerializer,
    NewsArticleListSerializer, SiteSettingSerializer,
    CompanyProfileSerializer,
    VisitSessionSerializer,
)
from . import search as site_search
from .specs import apply_spec_filters, fields_for_categories, filter_options
from .units import IMPERIAL, UNIT_SYSTEMS, public_registry
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

PRODUCT_ORDERING_FIELDS = {
    "model": "model",
    "newest": "-created_at",
    "published_at": "created_at",
    "-published_at": "-created_at",
    "updated_at": "updated_at",
    "-updated_at": "-updated_at",
    "relevance": "relevance",
}
MAX_PRODUCT_PAGE = 10_000


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


def requested_unit_system(params) -> str:
    system = params.get("units", "")
    if not system:
        return (
            SiteSetting.objects.filter(pk=1).values_list("default_unit_system", flat=True).first()
            or IMPERIAL
        )
    if system not in UNIT_SYSTEMS:
        raise ValidationError({"units": "Use imperial or metric."})
    return system


def precheck_product_filters(params) -> None:
    """Reject malformed filter text before any database query is made."""
    for name in ("category", "application"):
        if value := params.get(name):
            validate_slug_filter(value, field_name=name, max_length=50)
    if (system := params.get("units")) and system not in UNIT_SYSTEMS:
        raise ValidationError({"units": "Use imperial or metric."})
    if raw_query := params.get("q"):
        if len(raw_query) > site_search.MAX_QUERY_LENGTH:
            raise ValidationError({"q": "Search query must be at most 120 characters."})
        try:
            site_search.normalize_query(raw_query)
        except ValueError as exc:
            raise ValidationError({"q": str(exc)}) from exc
    for name in ("ordering", "sort"):
        if (value := params.get(name)) and (len(value) > 30 or value not in PRODUCT_ORDERING_FIELDS):
            raise ValidationError({name: "Unsupported product ordering."})
    for name in params:
        if name.startswith("spec."):
            value = params.get(name, "")
            if len(name) > 80 or len(value) > 255 or not value.isprintable():
                raise ValidationError({name[:80]: "Unsupported filter value."})


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


def spec_values_prefetch() -> Prefetch:
    return Prefetch(
        "spec_values",
        queryset=ProductSpecValue.objects.select_related("field"),
        to_attr="prefetched_spec_values",
    )


def category_queryset():
    return Category.objects.select_related("parent", "parent__parent").prefetch_related(
        "spec_fields",
        "parent__spec_fields",
        "parent__parent__spec_fields",
    )


def published_products():
    return Product.objects.filter(
        status=Product.Status.PUBLISHED,
        category__is_active=True,
    )


def category_with_descendants(slug: str) -> tuple[Category, set[int]]:
    category = Category.objects.filter(slug=slug, is_active=True).first()
    if category is None:
        return None, set()
    return category, category.descendant_ids()


class CategoryListView(generics.ListAPIView):
    serializer_class = CategorySerializer
    pagination_class = None

    def get_queryset(self):
        return category_queryset().filter(is_active=True).annotate(product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED)))


class CategoryDetailView(generics.RetrieveAPIView):
    serializer_class = CategorySerializer
    lookup_field = "slug"

    def get_queryset(self):
        return category_queryset().filter(is_active=True).annotate(
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
        precheck_product_filters(params)
        system = requested_unit_system(params)
        queryset = published_products()
        categories: list[Category] = []
        if category_slug := params.get("category"):
            category_slug = validate_slug_filter(
                category_slug,
                field_name="category",
                max_length=Category._meta.get_field("slug").max_length,
            )
            category, category_ids = category_with_descendants(category_slug)
            if category is None:
                queryset = queryset.none()
            else:
                categories = [category]
                queryset = queryset.filter(category_id__in=category_ids)
        if not categories:
            categories = list(Category.objects.filter(is_active=True).select_related("parent"))
        queryset = apply_spec_filters(queryset, params, fields_for_categories(categories), system)

        for parameter, field_name in (
            ("customizable", "is_customizable"),
            ("made_to_order", "made_to_order"),
            ("oem", "oem_available"),
        ):
            if raw := params.get(parameter):
                queryset = queryset.filter(**{field_name: validate_boolean_filter(raw, parameter)})
        if application := params.get("application"):
            application = validate_slug_filter(
                application,
                field_name="application",
                max_length=Application._meta.get_field("slug").max_length,
            )
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

        queryset = queryset.distinct().select_related("category", "category__parent").prefetch_related(
            active_applications_prefetch(),
            card_images_prefetch(),
            spec_values_prefetch(),
        )
        if raw_query := params.get("q"):
            if len(raw_query) > site_search.MAX_QUERY_LENGTH:
                raise ValidationError({"q": "Search query must be at most 120 characters."})
            try:
                parsed = site_search.parse_query(raw_query)
            except ValueError as exc:
                raise ValidationError({"q": str(exc)}) from exc
            if parsed.groups or parsed.quantities:
                ranked = site_search.search_products(queryset, parsed)
                if requested_ordering in (None, "relevance"):
                    return ranked
                return ranked.order_by(PRODUCT_ORDERING_FIELDS[requested_ordering], "pk")
        if requested_ordering == "relevance":
            requested_ordering = None
        ordering = PRODUCT_ORDERING_FIELDS.get(requested_ordering, "position")
        return queryset.order_by(ordering, "pk")


class ProductDetailView(generics.RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return published_products().select_related("category", "category__parent").prefetch_related(
            active_applications_prefetch(),
            spec_values_prefetch(),
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
            .select_related("category", "category__parent")
            .prefetch_related(
                active_applications_prefetch(),
                card_images_prefetch(),
                spec_values_prefetch(),
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
    categories = category_queryset().filter(is_active=True, parent__isnull=True).annotate(product_count=Count("products", filter=Q(products__status=Product.Status.PUBLISHED)))[:6]
    products = published_products().filter(featured=True).select_related("category", "category__parent").prefetch_related(
        active_applications_prefetch(),
        card_images_prefetch(),
        spec_values_prefetch(),
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
    categories = category_queryset().filter(is_active=True).annotate(
        product_count=Count(
            "products",
            filter=Q(products__status=Product.Status.PUBLISHED),
        )
    )
    has_applications = Application.objects.filter(is_active=True).exists()
    return Response(
        {
            "items": [
                {"label": "Products", "url": "/products"},
                *([{"label": "Applications", "url": "/applications"}] if has_applications else []),
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
    params = request.query_params
    system = requested_unit_system(params)
    products = published_products()
    if category_slug := params.get("category"):
        category_slug = validate_slug_filter(
            category_slug,
            field_name="category",
            max_length=Category._meta.get_field("slug").max_length,
        )
        category, category_ids = category_with_descendants(category_slug)
        if category is None:
            raise ValidationError({"category": "Unknown category."})
        categories = [category]
        products = products.filter(category_id__in=category_ids)
    else:
        categories = list(Category.objects.filter(is_active=True).select_related("parent"))
    return Response({
        "unit_system": system,
        "parameters": filter_options(products, fields_for_categories(categories), system),
        "made_to_order": [
            {"value": "true", "label": "Made to order / configurable"},
            {"value": "false", "label": "Standard only"},
        ],
    })


@api_view(["GET"])
def units_view(request):
    return Response({"systems": list(UNIT_SYSTEMS), "quantities": public_registry()})


@api_view(["GET"])
def faq_list_view(request):
    faqs = FAQ.objects.filter(is_published=True)
    return Response(FAQSerializer(faqs, many=True).data)


@api_view(["GET"])
def site_search_view(request):
    raw_query = request.query_params.get("q", "")
    if len(raw_query) > site_search.MAX_QUERY_LENGTH:
        raise ValidationError({"q": "Search query must be at most 120 characters."})
    try:
        parsed = site_search.parse_query(raw_query)
    except ValueError as exc:
        raise ValidationError({"q": str(exc)}) from exc
    if not parsed.groups and not parsed.quantities:
        return Response({"query": parsed.raw, "products": [], "news": [], "documents": [], "corrections": {}})
    products = site_search.search_products(
        published_products().select_related("category", "category__parent").prefetch_related(
            active_applications_prefetch(),
            card_images_prefetch(),
            spec_values_prefetch(),
        ),
        parsed,
    )[:48]
    news = site_search.search_news(parsed)
    documents = site_search.search_documents(parsed)
    return Response(
        {
            "query": parsed.raw,
            "corrections": parsed.corrections,
            "quantities": [
                {"text": mention.text, "quantity": mention.quantity} for mention in parsed.quantities
            ],
            "products": ProductListSerializer(products, many=True, context={"request": request}).data,
            "news": NewsArticleListSerializer(news, many=True, context={"request": request}).data,
            "documents": ProductDocumentSerializer(documents, many=True, context={"request": request}).data,
        }
    )


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

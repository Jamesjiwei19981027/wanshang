from __future__ import annotations

import logging
import secrets
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .file_security import validate_product_document_upload
from .storage import private_media_storage
from .validators import sanitize_upload_filename, validate_banner_link, validate_secure_public_url

logger = logging.getLogger(__name__)


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Category(TimeStampedModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=40, blank=True)
    image_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    meta_title = models.CharField(max_length=160, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)

    class Meta:
        ordering = ("position", "name")
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Application(TimeStampedModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    summary = models.TextField(blank=True)
    image_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    recommended_finish = models.CharField(max_length=120, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ("position", "name")

    def __str__(self) -> str:
        return self.name


class Product(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    class AxleType(models.TextChoices):
        STRAIGHT = "straight", "Straight axle"
        DROP = "drop", "Drop axle"
        TORSION = "torsion", "Torsion axle"
        OTHER = "other", "Other"

    class CapacityUnit(models.TextChoices):
        LB = "lb", "Pounds (lb)"
        KG = "kg", "Kilograms (kg)"

    class DimensionUnit(models.TextChoices):
        IN = "in", "Inches (in)"
        MM = "mm", "Millimetres (mm)"

    class BrakeType(models.TextChoices):
        NO_BRAKE = "No Brake", "No brake"
        ELECTRIC_DRUM = "Electric Drum", "Electric drum brake"
        HYDRAULIC_DRUM = "Hydraulic Drum", "Hydraulic drum brake"
        HYDRAULIC_DISC = "Hydraulic Disc", "Hydraulic disc brake"
        OTHER = "Other", "Other / engineering review"

    class SurfaceFinish(models.TextChoices):
        PAINTED = "Painted", "Painted"
        E_COATED = "E-Coated", "E-coated"
        POWDER_COATED = "Powder-Coated", "Powder-coated"
        ZINC_PLATED = "Zinc Plated", "Zinc plated"
        HOT_DIP_GALVANIZED = "Hot-Dip Galvanized", "Hot-dip galvanized"
        CUSTOM = "Custom Finish", "Custom finish"

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    applications = models.ManyToManyField(Application, blank=True, related_name="products")
    title = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    model = models.CharField(max_length=80)
    sku = models.CharField(max_length=80, unique=True)
    short_description = models.TextField()
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    rated_capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
    )
    capacity_unit = models.CharField(
        max_length=8,
        choices=CapacityUnit.choices,
        default=CapacityUnit.LB,
    )
    dimension_unit = models.CharField(
        max_length=8,
        choices=DimensionUnit.choices,
        default=DimensionUnit.IN,
    )
    axle_type = models.CharField(max_length=16, choices=AxleType.choices, default=AxleType.OTHER)
    brake_type = models.CharField(
        max_length=80,
        choices=BrakeType.choices,
        blank=True,
    )
    bolt_pattern = models.CharField(max_length=60, blank=True)
    stud_size = models.CharField(max_length=60, blank=True)
    grease_cap_type = models.CharField(max_length=80, blank=True)
    tube_size = models.CharField(max_length=60, blank=True)
    hub_face_length = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    spring_center = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    surface_finish = models.CharField(
        max_length=80,
        choices=SurfaceFinish.choices,
        blank=True,
    )
    is_customizable = models.BooleanField(default=True)
    oem_available = models.BooleanField(default=True)
    made_to_order = models.BooleanField(default=False)
    packaging = models.CharField(max_length=160, blank=True)
    featured = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    position = models.PositiveIntegerField(default=0)
    meta_title = models.CharField(max_length=160, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)

    class Meta:
        ordering = ("position", "title")
        indexes = [
            models.Index(fields=["status", "featured"]),
            models.Index(fields=["rated_capacity"]),
            models.Index(fields=["axle_type", "brake_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rated_capacity__isnull=True)
                | models.Q(rated_capacity__gt=0),
                name="product_capacity_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(hub_face_length__isnull=True)
                | models.Q(hub_face_length__gt=0),
                name="product_hub_face_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(spring_center__isnull=True)
                | models.Q(spring_center__gt=0),
                name="product_spring_center_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(brake_type="")
                | models.Q(
                    brake_type__in=(
                        "No Brake",
                        "Electric Drum",
                        "Hydraulic Drum",
                        "Hydraulic Disc",
                        "Other",
                    )
                ),
                name="product_brake_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(surface_finish="")
                | models.Q(
                    surface_finish__in=(
                        "Painted",
                        "E-Coated",
                        "Powder-Coated",
                        "Zinc Plated",
                        "Hot-Dip Galvanized",
                        "Custom Finish",
                    )
                ),
                name="product_surface_finish_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.model} — {self.title}"


class AxleSpec(TimeStampedModel):
    class TubeShape(models.TextChoices):
        ROUND = "Round", "Round"
        SQUARE = "Square", "Square"
        OTHER = "Other", "Other / engineering review"

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="axle_spec")
    overall_length = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    tube_shape = models.CharField(
        max_length=40,
        choices=TubeShape.choices,
        blank=True,
    )
    tube_wall_thickness = models.CharField(max_length=40, blank=True)
    drop_height = models.CharField(max_length=40, blank=True)
    hub_type = models.CharField(max_length=80, blank=True)
    spindle_type = models.CharField(max_length=80, blank=True)
    inner_bearing = models.CharField(max_length=80, blank=True)
    outer_bearing = models.CharField(max_length=80, blank=True)
    seal_size = models.CharField(max_length=60, blank=True)
    brake_size = models.CharField(max_length=60, blank=True)
    brake_voltage = models.CharField(max_length=40, blank=True)
    axle_material = models.CharField(max_length=80, blank=True)
    corrosion_protection = models.CharField(max_length=120, blank=True)
    moq = models.CharField(max_length=60, blank=True)
    lead_time = models.CharField(max_length=80, blank=True)
    loading_port = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(overall_length__isnull=True)
                | models.Q(overall_length__gt=0),
                name="axle_overall_length_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(tube_shape="")
                | models.Q(tube_shape__in=("Round", "Square", "Other")),
                name="axle_tube_shape_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Specs — {self.product.model}"


class ProductDocument(TimeStampedModel):
    class DocumentType(models.TextChoices):
        CATALOG = "catalog", "Catalog"
        DRAWING = "drawing", "Technical drawing"
        INSTALLATION = "installation", "Installation guide"
        MAINTENANCE = "maintenance", "Maintenance guide"
        CERTIFICATE = "certificate", "Certificate"
        PACKING = "packing", "Packing information"
        TEST = "test", "Test report"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=160)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    external_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    file = models.FileField(
        upload_to="documents/%Y/%m/",
        storage=private_media_storage,
        validators=[validate_product_document_upload],
        blank=True,
    )
    version = models.CharField(max_length=40, blank=True)
    downloads = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("document_type", "title")

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        if bool(self.external_url) == bool(self.file):
            raise ValidationError(
                "Configure exactly one product document source: file or external URL."
            )

    @property
    def previewable(self) -> bool:
        source = (
            urlparse(self.external_url).path
            if self.external_url
            else self.file.name if self.file else ""
        )
        return Path(source).suffix.lower() in {
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }

    @property
    def file_format(self) -> str:
        source = (
            urlparse(self.external_url).path
            if self.external_url
            else self.file.name if self.file else ""
        )
        return Path(source).suffix.lstrip(".").upper()[:30]

    @property
    def file_size(self) -> int | None:
        if not self.file:
            return None
        try:
            return self.file.size
        except OSError:
            return None


class ProductImage(TimeStampedModel):
    class ImageType(models.TextChoices):
        PRODUCT = "product", "Product"
        DETAIL = "detail", "Detail"
        DIMENSION = "dimension", "Dimension"
        INSTALLATION = "installation", "Installation"
        PACKAGE = "package", "Package"
        FACTORY = "factory", "Factory"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="products/%Y/%m/", blank=True)
    external_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    image_type = models.CharField(
        max_length=30,
        choices=ImageType.choices,
        default=ImageType.PRODUCT,
    )
    alt_text = models.CharField(max_length=255)
    caption = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "id")

    def clean(self) -> None:
        super().clean()
        if bool(self.external_url) == bool(self.image):
            raise ValidationError(
                "Configure exactly one product image source: upload or external URL."
            )

    def __str__(self) -> str:
        return f"{self.product.model} — {self.get_image_type_display()}"


class NewsArticle(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    summary = models.CharField(max_length=500)
    body = models.JSONField(default=list)
    category = models.CharField(max_length=100, blank=True)
    tags = models.JSONField(default=list, blank=True)
    reading_minutes = models.PositiveSmallIntegerField(default=1)
    cover_image = models.ImageField(upload_to="news/%Y/%m/", blank=True)
    cover_image_url = models.URLField(
        blank=True,
        validators=[validate_secure_public_url],
    )
    author = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    is_featured = models.BooleanField(default=False)
    seo_title = models.CharField(max_length=255, blank=True)
    seo_keywords = models.CharField(max_length=255, blank=True)
    seo_description = models.TextField(blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ("-published_at", "-created_at")

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.body, (list, dict)):
            raise ValidationError({"body": "Article body must be a JSON object or list."})
        if (
            not isinstance(self.tags, list)
            or len(self.tags) > 20
            or any(
                not isinstance(tag, str) or not tag.strip() or len(tag) > 60
                for tag in self.tags
            )
        ):
            raise ValidationError(
                {"tags": "Tags must be a list of up to 20 short labels."}
            )

    def save(self, *args, **kwargs) -> None:
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title


class Banner(TimeStampedModel):
    title = models.CharField(max_length=255)
    subtitle = models.TextField(blank=True)
    desktop_image = models.ImageField(upload_to="banners/%Y/%m/", blank=True)
    mobile_image = models.ImageField(upload_to="banners/%Y/%m/", blank=True)
    desktop_image_url = models.URLField(
        blank=True,
        validators=[validate_secure_public_url],
    )
    mobile_image_url = models.URLField(
        blank=True,
        validators=[validate_secure_public_url],
    )
    button_text = models.CharField(max_length=100, blank=True)
    button_link = models.CharField(
        max_length=255,
        blank=True,
        validators=[validate_banner_link],
    )
    secondary_button_text = models.CharField(max_length=100, blank=True)
    secondary_button_link = models.CharField(
        max_length=255,
        blank=True,
        validators=[validate_banner_link],
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("sort_order", "id")

    def clean(self) -> None:
        super().clean()
        if self.starts_at and self.ends_at and self.starts_at > self.ends_at:
            raise ValidationError({"ends_at": "End time must be after start time."})

    def __str__(self) -> str:
        return self.title


class SiteSetting(TimeStampedModel):
    site_name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    chinese_name = models.CharField(max_length=200, blank=True)
    tagline = models.CharField(max_length=200, blank=True)
    site_logo = models.ImageField(upload_to="site/", blank=True)
    site_logo_url = models.URLField(
        blank=True,
        validators=[validate_secure_public_url],
    )
    contact_email = models.EmailField(blank=True)
    sales_email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=50, blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    contact_title = models.CharField(max_length=100, blank=True)
    website_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    whatsapp_number = models.CharField(max_length=50, blank=True)
    whatsapp_link = models.URLField(
        blank=True,
        validators=[validate_secure_public_url],
    )
    address = models.TextField(blank=True)
    business_hours = models.CharField(max_length=150, blank=True)
    map_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    linkedin_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    global_seo_title = models.CharField(max_length=255, blank=True)
    global_seo_keywords = models.CharField(max_length=255, blank=True)
    global_seo_description = models.TextField(blank=True)
    default_og_image = models.ImageField(upload_to="site/", blank=True)
    default_og_image_url = models.URLField(
        blank=True,
        validators=[validate_secure_public_url],
    )
    google_analytics_id = models.CharField(max_length=100, blank=True)
    google_tag_manager_id = models.CharField(max_length=100, blank=True)
    footer_text = models.TextField(blank=True)
    inquiry_sales_subject_template = models.CharField(max_length=255, blank=True)
    inquiry_sales_intro_template = models.TextField(blank=True)
    inquiry_confirmation_subject_template = models.CharField(max_length=255, blank=True)
    inquiry_confirmation_intro_template = models.TextField(blank=True)
    contact_sales_subject_template = models.CharField(max_length=255, blank=True)
    contact_sales_intro_template = models.TextField(blank=True)
    contact_confirmation_subject_template = models.CharField(max_length=255, blank=True)
    contact_confirmation_intro_template = models.TextField(blank=True)

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.site_name


class CompanyProfile(TimeStampedModel):
    class BusinessRole(models.TextChoices):
        TRADING = "trading", "Trading company"
        MANUFACTURING = "manufacturing", "Manufacturer"
        BOTH = "both", "Manufacturer and trading company"

    headline = models.CharField(max_length=255, blank=True)
    summary = models.TextField(max_length=1500, blank=True)
    business_role = models.CharField(max_length=20, choices=BusinessRole.choices, blank=True)
    introduction = models.TextField(max_length=10000, blank=True)
    main_products = models.TextField(max_length=4000, blank=True)
    markets = models.TextField(max_length=4000, blank=True)
    strengths = models.TextField(max_length=6000, blank=True)
    quality_information = models.TextField(max_length=6000, blank=True)
    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=500, blank=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        verbose_name = "company profile"
        verbose_name_plural = "company profile"

    def clean(self) -> None:
        super().clean()
        if self.is_published and not self.introduction.strip():
            raise ValidationError({"introduction": "Add the verified company introduction before publishing."})

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return "About us"


class CompanyPhoto(TimeStampedModel):
    profile = models.ForeignKey(CompanyProfile, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="company/%Y/%m/")
    alt_text = models.CharField(max_length=255)
    caption = models.CharField(max_length=500, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "id")

    def __str__(self) -> str:
        return self.alt_text


class VisitSession(TimeStampedModel):
    class ConsentStatus(models.TextChoices):
        UNKNOWN = "unknown", "Unknown"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    session_key = models.CharField(max_length=100, unique=True)
    first_ip_hash = models.CharField(max_length=128, blank=True, editable=False)
    country = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    first_touch_source = models.CharField(max_length=120, blank=True)
    last_touch_source = models.CharField(max_length=120, blank=True)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)
    utm_term = models.CharField(max_length=150, blank=True)
    utm_content = models.CharField(max_length=150, blank=True)
    referrer_url = models.TextField(blank=True)
    first_landing_page = models.CharField(max_length=500, blank=True)
    last_landing_page = models.CharField(max_length=500, blank=True)
    user_agent = models.TextField(blank=True, editable=False)
    device_type = models.CharField(max_length=50, blank=True)
    browser = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=100, blank=True)
    consent_status = models.CharField(
        max_length=30,
        choices=ConsentStatus.choices,
        default=ConsentStatus.UNKNOWN,
    )
    started_at = models.DateTimeField(default=timezone.now, editable=False)
    last_seen_at = models.DateTimeField(default=timezone.now)
    converted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("-last_seen_at",)
        indexes = [
            models.Index(fields=["last_seen_at"]),
            models.Index(fields=["first_touch_source", "created_at"]),
        ]

    def __str__(self) -> str:
        return self.session_key


class PageView(TimeStampedModel):
    session = models.ForeignKey(
        VisitSession,
        on_delete=models.CASCADE,
        related_name="page_views",
    )
    path = models.CharField(max_length=500)
    page_title = models.CharField(max_length=255, blank=True)
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="page_views",
    )
    timestamp = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-timestamp",)
        indexes = [
            models.Index(fields=["session", "timestamp"]),
            models.Index(fields=["path", "timestamp"]),
        ]

    def __str__(self) -> str:
        return f"{self.path} at {self.timestamp:%Y-%m-%d %H:%M}"


class Inquiry(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        ASSIGNED = "assigned", "Assigned"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        QUOTED = "quoted", "Quoted"
        NEGOTIATING = "negotiating", "Negotiating"
        WON = "won", "Won"
        LOST = "lost", "Lost"
        CLOSED = "closed", "Closed"
        SPAM = "spam", "Spam"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    reference = models.CharField(max_length=32, unique=True, editable=False)
    submission_key_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    submission_payload_hash = models.CharField(max_length=64, blank=True, editable=False)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=60, blank=True)
    company = models.CharField(max_length=160, blank=True)
    country = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveIntegerField(null=True, blank=True)
    application = models.CharField(max_length=120, blank=True)
    destination_port = models.CharField(max_length=150, blank=True)
    target_delivery_date = models.DateField(null=True, blank=True)
    message = models.TextField(blank=True)
    specifications = models.JSONField(default=dict, blank=True)
    privacy_consent = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.NORMAL)
    assigned_to = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_inquiries")
    session = models.ForeignKey(
        VisitSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inquiries",
    )
    source_url = models.URLField(max_length=500, blank=True)
    referrer = models.TextField(blank=True)
    first_touch_source = models.CharField(max_length=120, blank=True)
    last_touch_source = models.CharField(max_length=120, blank=True)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)
    utm_term = models.CharField(max_length=150, blank=True)
    utm_content = models.CharField(max_length=150, blank=True)
    first_landing_page = models.CharField(max_length=500, blank=True)
    last_landing_page = models.CharField(max_length=500, blank=True)
    user_agent = models.TextField(blank=True, editable=False)
    ip_hash = models.CharField(max_length=128, blank=True)
    admin_notes = models.TextField(blank=True)
    next_followup_at = models.DateTimeField(null=True, blank=True)
    notification_queued_at = models.DateTimeField(null=True, blank=True, editable=False)
    notification_claimed_at = models.DateTimeField(null=True, blank=True, editable=False)
    notification_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    confirmation_claimed_at = models.DateTimeField(null=True, blank=True, editable=False)
    confirmation_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    notification_error = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "priority"]), models.Index(fields=["email", "created_at"])]

    def save(self, *args, **kwargs) -> None:
        if not self.reference:
            stamp = timezone.now().strftime("%y%m%d")
            self.reference = f"RFQ-{stamp}-{secrets.token_hex(6).upper()}"
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.reference} — {self.company or self.name}"


class InquiryItem(models.Model):
    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="inquiry_items")
    product_snapshot = models.JSONField(default=dict)
    quantity = models.PositiveIntegerField(default=1)
    selected_options = models.JSONField(default=dict, blank=True)
    requested_specs = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return f"{self.inquiry.reference} item {self.pk}"


class InquiryFollowup(TimeStampedModel):
    class ActionType(models.TextChoices):
        EMAIL = "email", "Email"
        CALL = "call", "Call"
        WHATSAPP = "whatsapp", "WhatsApp"
        QUOTE = "quote", "Quote"
        MEETING = "meeting", "Meeting"
        NOTE = "note", "Internal note"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="followups")
    user = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inquiry_followups",
    )
    action_type = models.CharField(
        max_length=30,
        choices=ActionType.choices,
        default=ActionType.NOTE,
    )
    content = models.TextField()
    next_followup_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.inquiry.reference} — {self.get_action_type_display()}"


def inquiry_attachment_path(instance: "InquiryAttachment", filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    random_name = f"{secrets.token_hex(16)}{suffix}"
    return f"quarantine/rfq/{timezone.now():%Y/%m}/{instance.inquiry.reference}/{random_name}"


class InquiryAttachment(TimeStampedModel):
    class ScanStatus(models.TextChoices):
        PENDING = "pending", "Pending scan"
        CLEAN = "clean", "Clean"
        INFECTED = "infected", "Infected"
        ERROR = "error", "Scan error"

    inquiry = models.ForeignKey(Inquiry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=inquiry_attachment_path, storage=private_media_storage)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField()
    declared_mime_type = models.CharField(max_length=120, blank=True, editable=False)
    scan_status = models.CharField(
        max_length=20,
        choices=ScanStatus.choices,
        default=ScanStatus.PENDING,
        db_index=True,
        editable=False,
    )
    scan_detail = models.CharField(max_length=500, blank=True, editable=False)
    scanned_at = models.DateTimeField(null=True, blank=True, editable=False)

    def __str__(self) -> str:
        return self.original_name


def _delete_private_file(storage, name: str) -> None:
    try:
        storage.delete(name)
    except Exception:
        logger.exception("Unable to delete protected file", extra={"file_name": name})


def _delete_product_document_file_if_unreferenced(storage, name: str) -> None:
    if ProductDocument.objects.filter(file=name).exists():
        return
    _delete_private_file(storage, name)


@receiver(post_delete, sender=InquiryAttachment)
def delete_inquiry_attachment_file(sender, instance: InquiryAttachment, **kwargs) -> None:
    """Delete private bytes only after the database deletion commits."""
    if not instance.file or not instance.file.name:
        return
    storage = instance.file.storage
    name = instance.file.name
    transaction.on_commit(lambda: _delete_private_file(storage, name))


@receiver(post_delete, sender=ProductDocument)
def delete_product_document_file(sender, instance: ProductDocument, **kwargs) -> None:
    """Delete protected product-document bytes after the database commit."""
    if not instance.file or not instance.file.name:
        return
    storage = instance.file.storage
    name = instance.file.name
    transaction.on_commit(
        lambda: _delete_product_document_file_if_unreferenced(storage, name)
    )


@receiver(pre_save, sender=ProductDocument)
def remember_replaced_product_document_file(
    sender,
    instance: ProductDocument,
    **kwargs,
) -> None:
    if not instance.pk:
        return
    instance._replaced_file_name = (
        ProductDocument.objects.filter(pk=instance.pk)
        .values_list("file", flat=True)
        .first()
        or ""
    )


@receiver(post_save, sender=ProductDocument)
def delete_replaced_product_document_file(
    sender,
    instance: ProductDocument,
    **kwargs,
) -> None:
    old_name = getattr(instance, "_replaced_file_name", "")
    new_name = instance.file.name if instance.file else ""
    if not old_name or old_name == new_name:
        return
    storage = instance.file.storage
    transaction.on_commit(
        lambda: _delete_product_document_file_if_unreferenced(
            storage,
            old_name,
        )
    )


class ContactMessage(TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "new", "New"
        ASSIGNED = "assigned", "Assigned"
        CONTACTED = "contacted", "Contacted"
        RESOLVED = "resolved", "Resolved"
        SPAM = "spam", "Spam"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    reference = models.CharField(max_length=32, unique=True, editable=False)
    submission_key_hash = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    submission_payload_hash = models.CharField(max_length=64, blank=True, editable=False)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=60, blank=True)
    company = models.CharField(max_length=160, blank=True)
    country = models.CharField(max_length=100, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    message = models.TextField()
    privacy_consent = models.BooleanField(default=False)
    is_resolved = models.BooleanField(default=False)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.NEW,
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    assigned_to = models.ForeignKey(
        "auth.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_contact_messages",
    )
    admin_notes = models.TextField(blank=True)
    session = models.ForeignKey(
        VisitSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contact_messages",
    )
    source_url = models.URLField(max_length=500, blank=True)
    referrer = models.TextField(blank=True)
    first_touch_source = models.CharField(max_length=120, blank=True)
    last_touch_source = models.CharField(max_length=120, blank=True)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)
    utm_term = models.CharField(max_length=150, blank=True)
    utm_content = models.CharField(max_length=150, blank=True)
    first_landing_page = models.CharField(max_length=500, blank=True)
    last_landing_page = models.CharField(max_length=500, blank=True)
    user_agent = models.TextField(blank=True, editable=False)
    ip_hash = models.CharField(max_length=128, blank=True, editable=False)
    notification_queued_at = models.DateTimeField(null=True, blank=True, editable=False)
    notification_claimed_at = models.DateTimeField(null=True, blank=True, editable=False)
    notification_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    confirmation_claimed_at = models.DateTimeField(null=True, blank=True, editable=False)
    confirmation_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    notification_error = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ("-created_at",)

    def save(self, *args, **kwargs) -> None:
        if not self.reference:
            stamp = timezone.now().strftime("%y%m%d")
            self.reference = f"MSG-{stamp}-{secrets.token_hex(6).upper()}"
        self.is_resolved = self.status == self.Status.RESOLVED
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.reference} — {self.name}"


class AnalyticsEvent(TimeStampedModel):
    event_name = models.CharField(max_length=80)
    path = models.CharField(max_length=500, blank=True)
    session_id = models.CharField(max_length=100, blank=True, db_index=True)
    visit_session = models.ForeignKey(
        VisitSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="analytics_events",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["event_name", "created_at"])]

    def __str__(self) -> str:
        return f"{self.event_name} at {self.created_at:%Y-%m-%d}"

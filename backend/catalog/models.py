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


def validate_translations(value: object) -> None:
    """Translations are {"es": {"name": "..."}, ...}; English is the base row."""
    if not isinstance(value, dict):
        raise ValidationError("Translations must be an object keyed by language code.")
    for language, fields in value.items():
        if (
            not isinstance(language, str)
            or not 2 <= len(language) <= 10
            or not all(part.isalnum() for part in language.split("-"))
        ):
            raise ValidationError("Use language codes such as es, pt or pt-BR.")
        if not isinstance(fields, dict) or not all(
            isinstance(key, str) and isinstance(text, str) for key, text in fields.items()
        ):
            raise ValidationError("Each language must map field names to text.")


class Category(TimeStampedModel):
    MAX_DEPTH = 5

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        help_text="Leave blank for a top-level category.",
    )
    inherit_parent_specs = models.BooleanField(
        default=True,
        help_text="Use the parent category's parameter list in addition to this category's own parameters.",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=40, blank=True)
    image_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    meta_title = models.CharField(max_length=160, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    translations = models.JSONField(default=dict, blank=True, validators=[validate_translations])

    class Meta:
        ordering = ("position", "name")
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return " → ".join(category.name for category in self.lineage())

    def clean(self) -> None:
        super().clean()
        seen = {self.pk} if self.pk else set()
        current = self.parent
        depth = 1
        while current is not None:
            if current.pk in seen:
                raise ValidationError({"parent": "A category cannot be placed inside itself."})
            seen.add(current.pk)
            depth += 1
            current = current.parent
        if depth > self.MAX_DEPTH:
            raise ValidationError({"parent": f"Categories can be nested at most {self.MAX_DEPTH} levels deep."})

    def lineage(self) -> list["Category"]:
        """Ancestors first, this category last."""
        chain: list[Category] = []
        current: Category | None = self
        guard = 0
        while current is not None and guard < self.MAX_DEPTH + 1:
            chain.append(current)
            current = current.parent
            guard += 1
        return list(reversed(chain))

    def descendant_ids(self) -> set[int]:
        ids = {self.pk}
        frontier = {self.pk}
        for _ in range(self.MAX_DEPTH):
            frontier = set(
                Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True)
            ) - ids
            if not frontier:
                break
            ids |= frontier
        return ids

    def spec_source_categories(self) -> list["Category"]:
        """Categories whose parameter lists apply here, ancestors first."""
        sources = [self]
        current = self
        while current.inherit_parent_specs and current.parent is not None:
            current = current.parent
            sources.append(current)
        return list(reversed(sources))

    def effective_spec_fields(self, *, products_only: bool = False) -> list["SpecField"]:
        fields: dict[str, SpecField] = {}
        for category in self.spec_source_categories():
            for field in category.spec_fields.all():
                # A child category may redefine an inherited key.
                fields[field.key] = field
        result = sorted(fields.values(), key=lambda field: (field.position, field.label))
        return [field for field in result if field.used_by_products] if products_only else result


class SpecField(TimeStampedModel):
    """One parameter in a category's parameter list."""

    class DataType(models.TextChoices):
        NUMBER = "number", "Number"
        TEXT = "text", "Text"
        CHOICE = "choice", "Choose from a list"
        BOOLEAN = "boolean", "Yes / no"

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="spec_fields")
    key = models.SlugField(
        max_length=60,
        help_text="Stable internal name, e.g. rated_capacity. Do not change after products use it.",
    )
    label = models.CharField(max_length=120)
    help_text = models.CharField(max_length=255, blank=True)
    data_type = models.CharField(max_length=10, choices=DataType.choices, default=DataType.TEXT)
    quantity = models.CharField(
        max_length=20,
        blank=True,
        help_text="For numbers measured in a unit (weight, length, pressure…). Leave blank for counts.",
    )
    imperial_unit = models.CharField(max_length=10, blank=True, help_text="Override the imperial display unit.")
    metric_unit = models.CharField(max_length=10, blank=True, help_text="Override the metric display unit.")
    choices = models.JSONField(
        default=list,
        blank=True,
        help_text='For "Choose from a list": ["Electric drum", "Hydraulic disc"].',
    )
    required = models.BooleanField(default=False)
    filterable = models.BooleanField(default=False, help_text="Offer this parameter as a catalogue filter.")
    show_on_card = models.BooleanField(default=False, help_text="Show on product cards in listings.")
    ask_in_rfq = models.BooleanField(default=False, help_text="Ask buyers for this parameter in the RFQ form.")
    used_by_products = models.BooleanField(
        default=True,
        help_text="Untick for questions asked only in the RFQ, e.g. the buyer's trailer weight.",
    )
    position = models.PositiveSmallIntegerField(default=0)
    translations = models.JSONField(default=dict, blank=True, validators=[validate_translations])

    class Meta:
        ordering = ("category", "position", "label")
        constraints = [
            models.UniqueConstraint(fields=["category", "key"], name="spec_field_unique_key_per_category"),
        ]

    def __str__(self) -> str:
        return f"{self.category.name}: {self.label}"

    def clean(self) -> None:
        from .units import QUANTITIES

        super().clean()
        errors: dict[str, str] = {}
        if self.data_type == self.DataType.NUMBER:
            if self.quantity:
                quantity = QUANTITIES.get(self.quantity)
                if quantity is None:
                    errors["quantity"] = "Select a supported quantity."
                else:
                    for name in ("imperial_unit", "metric_unit"):
                        code = getattr(self, name)
                        if code and quantity.unit(code) is None:
                            errors[name] = f"{code} does not measure {quantity.label.lower()}."
        elif self.quantity or self.imperial_unit or self.metric_unit:
            errors["quantity"] = "Only number parameters have units."
        if self.data_type == self.DataType.CHOICE:
            if (
                not isinstance(self.choices, list)
                or not self.choices
                or len(self.choices) > 100
                or not all(isinstance(item, str) and item.strip() and len(item) <= 120 for item in self.choices)
                or len({item.casefold() for item in self.choices}) != len(self.choices)
            ):
                errors["choices"] = "Enter a list of up to 100 distinct options."
        elif self.choices:
            errors["choices"] = "Only list parameters have options."
        if errors:
            raise ValidationError(errors)


class Application(TimeStampedModel):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    summary = models.TextField(blank=True)
    image_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    recommended_finish = models.CharField(max_length=120, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    translations = models.JSONField(default=dict, blank=True, validators=[validate_translations])

    class Meta:
        ordering = ("position", "name")

    def __str__(self) -> str:
        return self.name


class Product(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    applications = models.ManyToManyField(Application, blank=True, related_name="products")
    title = models.CharField(max_length=180)
    slug = models.SlugField(unique=True)
    model = models.CharField(max_length=80)
    sku = models.CharField(max_length=80, unique=True)
    short_description = models.TextField()
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True, validators=[validate_secure_public_url])
    is_customizable = models.BooleanField(default=True)
    oem_available = models.BooleanField(default=True)
    made_to_order = models.BooleanField(default=False)
    packaging = models.CharField(max_length=160, blank=True)
    moq = models.CharField("MOQ", max_length=60, blank=True)
    lead_time = models.CharField(max_length=80, blank=True)
    loading_port = models.CharField(max_length=80, blank=True)
    featured = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    position = models.PositiveIntegerField(default=0)
    meta_title = models.CharField(max_length=160, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    translations = models.JSONField(default=dict, blank=True, validators=[validate_translations])
    # Rebuilt from the product, its category path, applications, parameters
    # and documents whenever any of them changes. Used by site search.
    search_document = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ("position", "title")
        indexes = [
            models.Index(fields=["status", "featured"]),
        ]

    def __str__(self) -> str:
        return f"{self.model} — {self.title}"


class ProductSpecValue(models.Model):
    """A product's value for one parameter of its category's parameter list.

    Numbers keep the value and unit staff entered, plus the same value in the
    quantity's base unit so filters and search compare lb with kg directly.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="spec_values")
    field = models.ForeignKey(SpecField, on_delete=models.CASCADE, related_name="values")
    number_value = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    entered_unit = models.CharField(max_length=10, blank=True)
    base_value = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True, db_index=True)
    text_value = models.CharField(max_length=255, blank=True)
    boolean_value = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["product", "field"], name="product_spec_value_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.product.model} — {self.field.label}"

    def clean(self) -> None:
        from .units import QUANTITIES

        super().clean()
        field = self.field
        if field.data_type == SpecField.DataType.NUMBER:
            if field.quantity:
                quantity = QUANTITIES.get(field.quantity)
                if self.number_value is not None and (
                    quantity is None or quantity.unit(self.entered_unit) is None
                ):
                    raise ValidationError({"entered_unit": "Select a unit for this parameter."})
        elif field.data_type == SpecField.DataType.CHOICE:
            if self.text_value and self.text_value not in field.choices:
                raise ValidationError({"text_value": "Select one of the configured options."})

    def save(self, *args, **kwargs) -> None:
        from .units import to_base

        field = self.field
        if field.data_type == SpecField.DataType.NUMBER and self.number_value is not None:
            self.base_value = (
                to_base(self.number_value, field.quantity, self.entered_unit)
                if field.quantity
                else self.number_value
            )
        else:
            self.base_value = None
        super().save(*args, **kwargs)

    @property
    def is_empty(self) -> bool:
        return self.number_value is None and not self.text_value and self.boolean_value is None


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
    text_note = models.TextField(
        blank=True,
        max_length=20_000,
        help_text="Describe drawings or scanned files in words so search and the assistant can use them.",
    )
    extracted_text = models.TextField(blank=True, editable=False)
    extracted_at = models.DateTimeField(null=True, blank=True, editable=False)

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
    default_unit_system = models.CharField(
        max_length=10,
        choices=(("imperial", "Imperial (lb, in)"), ("metric", "Metric (kg, mm)")),
        default="imperial",
        help_text="Units shown to visitors by default. Visitors can switch.",
    )
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
    requested_categories = models.JSONField(default=list, blank=True)
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


class FAQ(TimeStampedModel):
    question = models.CharField(max_length=255)
    answer = models.TextField(max_length=10_000)
    group = models.CharField(max_length=80, blank=True, help_text="Optional heading, e.g. Ordering or Shipping.")
    position = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True, db_index=True)
    translations = models.JSONField(default=dict, blank=True, validators=[validate_translations])

    class Meta:
        ordering = ("position", "id")
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self) -> str:
        return self.question


class KnowledgeEntry(TimeStampedModel):
    """Information for the assistant that is not shown as a website page."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True)
    body = models.TextField(max_length=30_000)
    is_published = models.BooleanField(default=True, db_index=True)
    translations = models.JSONField(default=dict, blank=True, validators=[validate_translations])

    class Meta:
        ordering = ("title",)
        verbose_name_plural = "knowledge entries"

    def __str__(self) -> str:
        return self.title


class SearchSynonym(TimeStampedModel):
    terms = models.CharField(
        max_length=500,
        help_text="Comma-separated words or phrases that mean the same thing, e.g. tyre, tire.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("terms",)

    def __str__(self) -> str:
        return self.terms

    def term_list(self) -> list[str]:
        return [term.strip().casefold() for term in self.terms.split(",") if term.strip()]

    def clean(self) -> None:
        super().clean()
        if len(self.term_list()) < 2:
            raise ValidationError({"terms": "Enter at least two terms separated by commas."})


class SearchMiss(models.Model):
    """Searches that returned nothing, from visitors who accepted analytics."""

    query = models.CharField(max_length=120, unique=True)
    count = models.PositiveIntegerField(default=1)
    first_seen_at = models.DateTimeField(default=timezone.now, editable=False)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved = models.BooleanField(default=False, help_text="Tick after adding a synonym or product.")

    class Meta:
        ordering = ("-count", "-last_seen_at")
        verbose_name = "search without results"
        verbose_name_plural = "searches without results"

    def __str__(self) -> str:
        return self.query

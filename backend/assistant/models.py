from __future__ import annotations

import secrets
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

DEFAULT_PRIVACY_NOTICE = (
    "Conversations are saved so we can improve answers and follow up on your enquiry. "
    "Questions are processed by our AI provider. See our privacy policy for details."
)
DEFAULT_WELCOME = "Hi! I can help you find products, compare specifications and prepare a quotation request."


class AssistantSetting(models.Model):
    """Singleton (pk=1) with the switches and limits staff can change."""

    enabled = models.BooleanField(
        default=True,
        help_text="Master switch. When off, the assistant entry disappears and the contact options remain.",
    )
    model_name = models.CharField(max_length=80, default="deepseek-chat")
    visitor_daily_limit = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(1), MaxValueValidator(1000)])
    ip_daily_limit = models.PositiveSmallIntegerField(default=25, validators=[MinValueValidator(1), MaxValueValidator(5000)])
    max_question_chars = models.PositiveSmallIntegerField(default=500, validators=[MinValueValidator(50), MaxValueValidator(4000)])
    max_lookups = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text="How many times the assistant may search or read site content while answering one question.",
    )
    idle_timeout_seconds = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(5), MaxValueValidator(300)],
        help_text="Disconnect when the AI service sends nothing for this long. There is no limit on total answer time.",
    )
    daily_cost_cap = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Pause the assistant for the rest of the day once today's cost reaches this amount. Blank = no cap.",
    )
    currency = models.CharField(max_length=3, default="CNY")
    price_input_cache_hit = models.DecimalField(
        "Price per million input tokens (cache hit)", max_digits=10, decimal_places=4, default=Decimal("0"),
    )
    price_input_cache_miss = models.DecimalField(
        "Price per million input tokens (cache miss)", max_digits=10, decimal_places=4, default=Decimal("0"),
    )
    price_output = models.DecimalField("Price per million output tokens", max_digits=10, decimal_places=4, default=Decimal("0"))
    conversation_retention_days = models.PositiveSmallIntegerField(default=90, validators=[MinValueValidator(1)])
    lead_retention_days = models.PositiveSmallIntegerField(
        default=365,
        validators=[MinValueValidator(1)],
        help_text="Conversations where the visitor left contact details but did not submit an RFQ.",
    )
    welcome_message = models.CharField(max_length=300, default=DEFAULT_WELCOME)
    privacy_notice = models.TextField(max_length=1000, default=DEFAULT_PRIVACY_NOTICE)
    extra_instructions = models.TextField(
        blank=True,
        max_length=4000,
        help_text="Optional guidance added to the assistant's instructions, e.g. preferred wording.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "assistant settings"
        verbose_name_plural = "assistant settings"

    def __str__(self) -> str:
        return "Assistant settings"

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "AssistantSetting":
        instance = cls.objects.filter(pk=1).first()
        return instance or cls(pk=1)

    @property
    def prices_configured(self) -> bool:
        return any((self.price_input_cache_hit, self.price_input_cache_miss, self.price_output))


class QuickPrompt(models.Model):
    class Page(models.TextChoices):
        ANY = "any", "Every page"
        HOME = "home", "Home page"
        PRODUCTS = "products", "Product list pages"
        PRODUCT = "product", "Product detail pages"
        RFQ = "rfq", "Request a quote page"
        OTHER = "other", "Other pages"

    page = models.CharField(max_length=10, choices=Page.choices, default=Page.ANY)
    text = models.CharField(max_length=120)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("page", "position", "id")

    def __str__(self) -> str:
        return self.text


def new_public_id() -> str:
    return secrets.token_hex(16)


class Conversation(models.Model):
    class Kind(models.TextChoices):
        GENERAL = "general", "General enquiry"
        LEAD = "lead", "Potential customer (contact left, no RFQ)"
        INQUIRY = "inquiry", "Linked to an RFQ"

    public_id = models.CharField(max_length=32, unique=True, default=new_public_id, editable=False)
    visitor_hash = models.CharField(max_length=64, db_index=True, editable=False)
    ip_hash = models.CharField(max_length=128, blank=True, editable=False)
    page_path = models.CharField(max_length=500, blank=True)
    language = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.GENERAL, db_index=True)
    contact_details = models.CharField(max_length=300, blank=True, help_text="Email or phone the visitor typed.")
    inquiry = models.ForeignKey(
        "catalog.Inquiry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_conversations",
    )
    message_count = models.PositiveIntegerField(default=0)
    total_cost = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-last_message_at",)

    def __str__(self) -> str:
        return f"Conversation {self.public_id[:8]} ({self.get_kind_display()})"


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Visitor"
        ASSISTANT = "assistant", "Assistant"

    class Rating(models.TextChoices):
        UNRATED = "unrated", "Not reviewed"
        CORRECT = "correct", "Correct"
        INCORRECT = "incorrect", "Incorrect"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    citations = models.JSONField(default=list, blank=True)
    lookups = models.JSONField(default=list, blank=True, help_text="What the assistant searched and read.")
    rfq_draft = models.JSONField(default=dict, blank=True)
    prompt_cache_hit_tokens = models.PositiveIntegerField(default=0)
    prompt_cache_miss_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal("0"))
    error = models.CharField(max_length=300, blank=True)
    rating = models.CharField(max_length=10, choices=Rating.choices, default=Rating.UNRATED, db_index=True)
    rating_note = models.TextField(blank=True, help_text="What was wrong and what the right answer is.")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("created_at", "id")

    def __str__(self) -> str:
        return f"{self.get_role_display()}: {self.content[:60]}"

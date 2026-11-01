"""Contact details in conversations, RFQ drafts and links to submitted RFQs."""

from __future__ import annotations

import re
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from catalog.models import Application, Category, Inquiry, Product

from .models import AssistantSetting, Conversation

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?<![\w])\+?\d[\d\s().-]{6,18}\d(?![\w])")
RFQ_DRAFT_TEXT_FIELDS = {
    "name": 120,
    "email": 254,
    "phone": 60,
    "company": 160,
    "country": 100,
    "destination_port": 150,
    "message": 4000,
}


def detect_contact(text: str) -> str:
    found = EMAIL_PATTERN.findall(text)
    for match in PHONE_PATTERN.findall(text):
        if sum(character.isdigit() for character in match) >= 7:
            found.append(match.strip())
    return ", ".join(dict.fromkeys(found))[:300]


def remember_contact(conversation: Conversation, text: str) -> bool:
    details = detect_contact(text)
    if not details:
        return False
    merged = ", ".join(dict.fromkeys(filter(None, [*conversation.contact_details.split(", "), *details.split(", ")])))
    conversation.contact_details = merged[:300]
    if conversation.kind == Conversation.Kind.GENERAL:
        conversation.kind = Conversation.Kind.LEAD
    conversation.save(update_fields=["contact_details", "kind"])
    return True


def build_rfq_draft(arguments: dict) -> dict:
    """Turn the assistant's suggestion into a draft the RFQ form can prefill.

    The draft is only shown to the visitor; it is never submitted here. The
    visitor reviews it, accepts the privacy notice and passes the human check.
    Unknown categories, products and parameters are dropped.
    """
    draft: dict = {}
    for name, limit in RFQ_DRAFT_TEXT_FIELDS.items():
        value = arguments.get(name)
        if isinstance(value, str) and value.strip():
            draft[name] = value.strip()[:limit]
    quantity = arguments.get("quantity")
    if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) and 1 <= quantity <= 100_000_000:
        draft["quantity"] = int(quantity)
    application = arguments.get("application")
    if isinstance(application, str) and Application.objects.filter(slug=application, is_active=True).exists():
        draft["application"] = application

    categories = [slug for slug in arguments.get("categories", []) if isinstance(slug, str)][:10]
    category_objects = {category.slug: category for category in Category.objects.filter(slug__in=categories, is_active=True)}
    draft["requested_categories"] = [slug for slug in categories if slug in category_objects]

    products = []
    for slug in [slug for slug in arguments.get("products", []) if isinstance(slug, str)][:20]:
        product = Product.objects.filter(slug=slug, status=Product.Status.PUBLISHED, category__is_active=True).select_related("category").first()
        if product:
            products.append({"id": product.id, "slug": product.slug, "title": product.title, "model": product.model, "sku": product.sku, "category": product.category.slug})
            if product.category.slug not in draft["requested_categories"]:
                draft["requested_categories"].append(product.category.slug)
                category_objects[product.category.slug] = product.category
    draft["products"] = products

    specifications: dict[str, dict[str, str]] = {}
    raw_specs = arguments.get("specifications")
    if isinstance(raw_specs, dict):
        for slug, values in raw_specs.items():
            category = category_objects.get(slug)
            if category is None or not isinstance(values, dict):
                continue
            allowed = {field.key for field in category.effective_spec_fields() if field.ask_in_rfq}
            kept = {key: str(value)[:160] for key, value in values.items() if key in allowed and isinstance(value, (str, int, float)) and not isinstance(value, bool)}
            if kept:
                specifications[slug] = kept
    draft["specifications"] = specifications
    return draft


def link_conversation_to_inquiry(public_id: str, inquiry: Inquiry, request) -> None:
    """Attach an assistant conversation to the RFQ submitted from its panel."""
    from catalog.attribution import hash_client_ip

    if not isinstance(public_id, str) or not re.fullmatch(r"[0-9a-f]{32}", public_id):
        return
    # The same browser network that held the conversation must submit the RFQ.
    Conversation.objects.filter(
        public_id=public_id,
        inquiry__isnull=True,
        ip_hash=hash_client_ip(request),
        last_message_at__gte=timezone.now() - timedelta(days=2),
    ).update(inquiry=inquiry, kind=Conversation.Kind.INQUIRY)


PURGE_LOCK_KEY = "assistant:retention:last-run"


def purge_expired_conversations(now=None) -> int:
    """Delete conversations past their retention period; returns the count."""
    setting = AssistantSetting.load()
    now = now or timezone.now()
    general_cutoff = now - timedelta(days=setting.conversation_retention_days)
    lead_cutoff = now - timedelta(days=setting.lead_retention_days)
    expired = Conversation.objects.filter(inquiry__isnull=True).filter(
        Q(kind=Conversation.Kind.GENERAL, last_message_at__lt=general_cutoff)
        | Q(kind=Conversation.Kind.LEAD, last_message_at__lt=lead_cutoff)
        # An RFQ that was later deleted leaves a conversation without a link.
        | Q(kind=Conversation.Kind.INQUIRY, last_message_at__lt=lead_cutoff)
    )
    deleted, _ = expired.delete()
    return deleted


def purge_once_a_day() -> None:
    if cache.add(PURGE_LOCK_KEY, "1", 24 * 60 * 60):
        purge_expired_conversations()

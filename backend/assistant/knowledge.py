"""Read-only knowledge base built on demand from published website data.

Nothing here is written by a language model. Every page is rendered from
the database in a fixed format, so a product change or unpublish is visible
to the assistant immediately. Only published, active content is reachable.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q
from django.utils import timezone

from catalog import search as site_search
from catalog.models import (
    FAQ,
    Application,
    Category,
    CompanyProfile,
    KnowledgeEntry,
    NewsArticle,
    Product,
    ProductDocument,
    SiteSetting,
)
from catalog.specs import serialize_field, serialize_spec_values

MAX_PAGE_CHARACTERS = 6_000
MAX_RESULTS = 8
SUMMARY_CHARACTERS = 220


@dataclass(frozen=True)
class Page:
    ref: str
    kind: str
    title: str
    url: str
    text: str


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _published_products():
    return Product.objects.filter(status=Product.Status.PUBLISHED, category__is_active=True)


def product_url(product: Product) -> str:
    return f"/products/{product.category.slug}/{product.slug}"


def _render_product(product: Product) -> str:
    lines = [
        f"Product: {product.title}",
        f"Model: {product.model} | Part number: {product.sku}",
        "Category: " + " > ".join(category.name for category in product.category.lineage()),
        f"Summary: {product.short_description}",
    ]
    if product.description:
        lines.append(f"Description: {product.description}")
    specs = serialize_spec_values(product)
    if specs:
        lines.append("Parameters (imperial | metric):")
        for row in specs:
            imperial, metric = row["display"]["imperial"], row["display"]["metric"]
            lines.append(f"- {row['label']}: {imperial}" + (f" | {metric}" if metric != imperial else ""))
    applications = [application.name for application in product.applications.filter(is_active=True)]
    if applications:
        lines.append("Applications: " + ", ".join(applications))
    flags = [
        "customizable" if product.is_customizable else "standard configuration",
        "OEM available" if product.oem_available else "",
        "made to order" if product.made_to_order else "",
    ]
    lines.append("Configuration: " + ", ".join(flag for flag in flags if flag))
    for label, value in (("MOQ", product.moq), ("Lead time", product.lead_time), ("Loading port", product.loading_port), ("Packaging", product.packaging)):
        if value:
            lines.append(f"{label}: {value}")
    documents = list(product.documents.filter(is_active=True))
    if documents:
        lines.append("Documents: " + "; ".join(f"{document.title} [ref document:{document.pk}]" for document in documents))
    return "\n".join(lines)


def _render_category(category: Category) -> str:
    lines = [
        "Category: " + " > ".join(item.name for item in category.lineage()),
        f"Description: {category.description}" if category.description else "",
    ]
    children = list(category.children.filter(is_active=True))
    if children:
        lines.append("Subcategories: " + ", ".join(f"{child.name} [ref category:{child.slug}]" for child in children))
    fields = category.effective_spec_fields()
    if fields:
        lines.append("Parameter list (key: label [unit type]; RFQ = asked in quotation requests):")
        for field in fields:
            data = serialize_field(field)
            unit = f" [{field.quantity}]" if field.quantity else ""
            options = f" options: {', '.join(field.choices)}" if field.choices else ""
            rfq = " RFQ" if field.ask_in_rfq else ""
            lines.append(f"- {data['key']}: {data['label']}{unit}{options}{rfq}")
    count = _published_products().filter(category_id__in=category.descendant_ids()).count()
    lines.append(f"Published products: {count}")
    return "\n".join(line for line in lines if line)


def _document_text(document: ProductDocument) -> str:
    lines = [
        f"Document: {document.title} ({document.get_document_type_display()})",
        f"Product: {document.product.title} ({document.product.model}) [ref product:{document.product.slug}]",
    ]
    if document.version:
        lines.append(f"Version: {document.version}")
    if document.text_note:
        lines.append(f"Description: {document.text_note}")
    if document.extracted_text:
        lines.append(f"Text: {document.extracted_text}")
    return "\n".join(lines)


def _company_text() -> str:
    site = SiteSetting.objects.filter(pk=1).first()
    profile = CompanyProfile.objects.filter(pk=1, is_published=True).first()
    lines: list[str] = []
    if site:
        lines += [f"Company: {site.legal_name or site.site_name}", f"Address: {site.address}" if site.address else ""]
    if profile:
        for label, value in (
            ("Introduction", profile.introduction),
            ("Main products", profile.main_products),
            ("Markets", profile.markets),
            ("Strengths", profile.strengths),
            ("Quality", profile.quality_information),
        ):
            if value:
                lines.append(f"{label}: {value}")
    return "\n".join(line for line in lines if line)


def read_page(ref: str) -> Page | None:
    kind, _, key = (ref or "").partition(":")
    key = key.strip()
    if not key or len(key) > 200:
        return None
    if kind == "product":
        product = _published_products().select_related("category").filter(slug=key).first()
        if product:
            return Page(ref, kind, product.title, product_url(product), _render_product(product))
    elif kind == "category":
        category = Category.objects.filter(slug=key, is_active=True).first()
        if category:
            return Page(ref, kind, category.name, f"/products/{category.slug}", _render_category(category))
    elif kind == "news":
        article = NewsArticle.objects.filter(
            slug=key, status=NewsArticle.Status.PUBLISHED, published_at__lte=timezone.now()
        ).first()
        if article:
            body = article.body if isinstance(article.body, str) else _flatten_json(article.body)
            return Page(ref, kind, article.title, f"/news/{article.slug}", f"News: {article.title}\nSummary: {article.summary}\n{body}")
    elif kind == "faq" and key.isdecimal():
        faq = FAQ.objects.filter(pk=int(key), is_published=True).first()
        if faq:
            return Page(ref, kind, faq.question, "/faq", f"Question: {faq.question}\nAnswer: {faq.answer}")
    elif kind == "knowledge":
        entry = KnowledgeEntry.objects.filter(slug=key, is_published=True).first()
        if entry:
            return Page(ref, kind, entry.title, "", f"{entry.title}\n{entry.body}")
    elif kind == "document" and key.isdecimal():
        document = ProductDocument.objects.filter(
            pk=int(key),
            is_active=True,
            product__status=Product.Status.PUBLISHED,
            product__category__is_active=True,
        ).select_related("product", "product__category").first()
        if document:
            return Page(ref, kind, document.title, f"/api/documents/{document.pk}/download/", _document_text(document))
    elif kind == "company" and key == "profile":
        text = _company_text()
        if text:
            return Page(ref, kind, "About us", "/about", text)
    elif kind == "application":
        application = Application.objects.filter(slug=key, is_active=True).first()
        if application:
            products = _published_products().filter(applications=application)[:20]
            listing = "; ".join(f"{product.title} [ref product:{product.slug}]" for product in products)
            return Page(ref, kind, application.name, f"/applications/{application.slug}", f"Application: {application.name}\n{application.summary}\nProducts: {listing}")
    return None


def _flatten_json(value: object) -> str:
    parts: list[str] = []
    stack = [value]
    while stack and len(parts) < 400:
        current = stack.pop()
        if isinstance(current, str):
            parts.append(current)
        elif isinstance(current, dict):
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return " ".join(parts)


def page_payload(page: Page) -> str:
    text = page.text if len(page.text) <= MAX_PAGE_CHARACTERS else page.text[:MAX_PAGE_CHARACTERS] + "\n[truncated]"
    return f"[ref {page.ref}]\n{text}"


def search(query: str, category: str = "") -> list[dict]:
    """Search everything the assistant may read; short summaries only."""
    try:
        parsed = site_search.parse_query(query)
    except ValueError:
        return []
    results: list[dict] = []
    products = _published_products().select_related("category")
    if category:
        selected = Category.objects.filter(slug=category, is_active=True).first()
        if selected:
            products = products.filter(category_id__in=selected.descendant_ids())
    for product in site_search.search_products(products, parsed)[:MAX_RESULTS]:
        results.append({"ref": f"product:{product.slug}", "type": "product", "title": f"{product.title} ({product.model})", "summary": _clip(product.short_description, SUMMARY_CHARACTERS)})
    if parsed.groups:
        condition = Q()
        for alternatives in parsed.groups:
            for alternative in alternatives:
                condition |= Q(name__icontains=alternative) | Q(description__icontains=alternative)
        for item in Category.objects.filter(condition, is_active=True)[:4]:
            results.append({"ref": f"category:{item.slug}", "type": "category", "title": item.name, "summary": _clip(item.description, SUMMARY_CHARACTERS)})
        faqs, entries = site_search.search_knowledge(parsed, limit=4)
        results += [{"ref": f"faq:{faq.pk}", "type": "faq", "title": faq.question, "summary": _clip(faq.answer, SUMMARY_CHARACTERS)} for faq in faqs]
        results += [{"ref": f"knowledge:{entry.slug}", "type": "knowledge", "title": entry.title, "summary": _clip(entry.body, SUMMARY_CHARACTERS)} for entry in entries]
        results += [{"ref": f"news:{article.slug}", "type": "news", "title": article.title, "summary": _clip(article.summary, SUMMARY_CHARACTERS)} for article in site_search.search_news(parsed, limit=3)]
        results += [{"ref": f"document:{document.pk}", "type": "document", "title": document.title, "summary": _clip(document.text_note or document.product.title, SUMMARY_CHARACTERS)} for document in site_search.search_documents(parsed, limit=3)]
    return results[: MAX_RESULTS * 2]


def catalogue_overview() -> str:
    """Short, stable context listing the top of the catalogue tree."""
    lines = []
    for category in Category.objects.filter(is_active=True).select_related("parent").order_by("position", "name"):
        depth = len(category.lineage()) - 1
        lines.append(f"{'  ' * depth}- {category.name} [ref category:{category.slug}]")
    applications = list(Application.objects.filter(is_active=True).values_list("name", "slug"))
    if applications:
        lines.append("Applications: " + ", ".join(f"{name} [ref application:{slug}]" for name, slug in applications))
    return "\n".join(lines)


def contact_summary() -> str:
    site = SiteSetting.objects.filter(pk=1).first()
    if site is None:
        return "Use the RFQ form on the website."
    parts = [
        f"Email: {site.sales_email or site.contact_email}" if (site.sales_email or site.contact_email) else "",
        f"Phone: {site.phone_number}" if site.phone_number else "",
        f"WhatsApp: {site.whatsapp_number}" if site.whatsapp_number else "",
        "RFQ form: /request-a-quote",
    ]
    return " | ".join(part for part in parts if part)

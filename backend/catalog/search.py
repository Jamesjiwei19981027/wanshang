"""Site search: word matching, units, typo tolerance, synonyms and ranking.

Search runs in the database without a language model, so results are
immediate and free. It works on PostgreSQL and on SQLite (tests).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, QuerySet, Value, When
from django.utils import timezone

from .models import (
    FAQ,
    KnowledgeEntry,
    NewsArticle,
    Product,
    ProductDocument,
    ProductSpecValue,
    SearchMiss,
    SearchSynonym,
    SpecField,
)
from .units import find_quantities

MAX_QUERY_LENGTH = 120
MAX_TERMS = 8
WORD_PATTERN = re.compile(r"[\w][\w./#+-]*", re.UNICODE)
STOP_WORDS = frozenset(
    "a an and are as at be by for from i in is it me my of on or our the to we what which with you your need want looking "
    "please can do does have has any".split()
)
VOCABULARY_CACHE_KEY = "catalog:search:vocabulary"
SYNONYM_CACHE_KEY = "catalog:search:synonyms"
SEARCH_CACHE_TIMEOUT = 600
# A typed quantity matches a spec value within this relative spread.
QUANTITY_TOLERANCE = Decimal("0.02")


@dataclass
class ParsedQuery:
    raw: str
    terms: list[str] = field(default_factory=list)
    # Each group is a set of alternatives for one typed word (synonyms, typo fix).
    groups: list[list[str]] = field(default_factory=list)
    quantities: list = field(default_factory=list)
    corrections: dict[str, str] = field(default_factory=dict)


def normalize_query(raw: str) -> str:
    query = " ".join(raw.split())[:MAX_QUERY_LENGTH]
    if query and not query.isprintable():
        raise ValueError("Search query contains unsupported characters.")
    return query


def invalidate_search_caches() -> None:
    cache.delete_many([VOCABULARY_CACHE_KEY, SYNONYM_CACHE_KEY])


def synonym_groups() -> list[list[str]]:
    groups = cache.get(SYNONYM_CACHE_KEY)
    if groups is None:
        groups = [synonym.term_list() for synonym in SearchSynonym.objects.filter(is_active=True)]
        cache.set(SYNONYM_CACHE_KEY, groups, SEARCH_CACHE_TIMEOUT)
    return groups


def vocabulary() -> set[str]:
    words = cache.get(VOCABULARY_CACHE_KEY)
    if words is None:
        words = set()
        documents = Product.objects.filter(
            status=Product.Status.PUBLISHED,
            category__is_active=True,
        ).values_list("search_document", flat=True)
        for document in documents.iterator():
            words.update(word.casefold() for word in WORD_PATTERN.findall(document) if len(word) >= 3)
        cache.set(VOCABULARY_CACHE_KEY, words, SEARCH_CACHE_TIMEOUT)
    return words


def parse_query(raw: str) -> ParsedQuery:
    query = normalize_query(raw)
    parsed = ParsedQuery(raw=query)
    if not query:
        return parsed
    parsed.quantities = find_quantities(query)
    remainder = query
    for mention in parsed.quantities:
        remainder = remainder.replace(mention.text, " ")
    lowered = remainder.casefold()

    groups = synonym_groups()
    used_phrases: set[str] = set()
    # Multi-word synonyms are matched as phrases before splitting into words.
    for group in groups:
        for term in group:
            if " " in term and term in lowered and term not in used_phrases:
                parsed.groups.append(list(dict.fromkeys(group)))
                parsed.terms.append(term)
                used_phrases.add(term)
                lowered = lowered.replace(term, " ")
                break

    words = [word for word in WORD_PATTERN.findall(lowered) if word not in STOP_WORDS]
    known = vocabulary()
    for word in words[:MAX_TERMS]:
        alternatives = [word]
        for group in groups:
            if word in group:
                alternatives.extend(term for term in group if term not in alternatives)
        if len(word) >= 4 and known and word not in known and not any(alt in known for alt in alternatives[1:]):
            close = difflib.get_close_matches(word, known, n=1, cutoff=0.8)
            if close:
                parsed.corrections[word] = close[0]
                alternatives.append(close[0])
        parsed.terms.append(word)
        parsed.groups.append(alternatives)
    parsed.groups = parsed.groups[:MAX_TERMS]
    return parsed


def _any_of(field_name: str, alternatives: list[str]) -> Q:
    condition = Q()
    for alternative in alternatives:
        condition |= Q(**{f"{field_name}__icontains": alternative})
    return condition


def _quantity_filter(queryset: QuerySet, parsed: ParsedQuery) -> QuerySet:
    by_text: dict[str, list] = {}
    for mention in parsed.quantities:
        by_text.setdefault(mention.text, []).append(mention)
    for mentions in by_text.values():
        condition = Q()
        for mention in mentions:
            spread = abs(mention.base_value) * QUANTITY_TOLERANCE
            condition |= Q(
                field__quantity=mention.quantity,
                base_value__gte=mention.base_value - spread,
                base_value__lte=mention.base_value + spread,
            )
        queryset = queryset.filter(
            Exists(
                ProductSpecValue.objects.filter(
                    product=OuterRef("pk"),
                    field__data_type=SpecField.DataType.NUMBER,
                ).filter(condition)
            )
        )
    return queryset


def search_products(queryset: QuerySet, parsed: ParsedQuery) -> QuerySet:
    """Filter and rank published products; returns an ordered queryset."""
    if not parsed.groups and not parsed.quantities:
        return queryset.none()
    if parsed.quantities:
        queryset = _quantity_filter(queryset, parsed)
    if not parsed.groups:
        return queryset.annotate(search_score=Value(1, output_field=IntegerField())).order_by("position", "title")

    all_terms = Q()
    any_term = Q()
    for alternatives in parsed.groups:
        all_terms &= _any_of("search_document", alternatives)
        any_term |= _any_of("search_document", alternatives)
    strict = queryset.filter(all_terms)
    candidates = strict if strict.exists() else queryset.filter(any_term)

    phrase = " ".join(parsed.terms)
    scores = [
        When(Q(model__iexact=parsed.raw) | Q(sku__iexact=parsed.raw), then=Value(1000)),
    ]
    score = Case(*scores, default=Value(0), output_field=IntegerField())
    for alternatives in parsed.groups:
        score = score + Case(
            When(_any_of("model", alternatives) | _any_of("sku", alternatives), then=Value(60)),
            default=Value(0),
            output_field=IntegerField(),
        ) + Case(
            When(_any_of("title", alternatives), then=Value(25)),
            default=Value(0),
            output_field=IntegerField(),
        ) + Case(
            When(_any_of("search_document", alternatives), then=Value(5)),
            default=Value(0),
            output_field=IntegerField(),
        )
    if len(parsed.terms) > 1:
        score = score + Case(
            When(title__icontains=phrase, then=Value(40)),
            default=Value(0),
            output_field=IntegerField(),
        )
    return candidates.annotate(search_score=score).order_by("-search_score", "position", "title")


def _rank_texts(items: list, parsed: ParsedQuery, title_attr: str, text_attrs: tuple[str, ...]) -> list:
    ranked = []
    for item in items:
        title = getattr(item, title_attr, "").casefold()
        text = " ".join(str(getattr(item, attr, "")) for attr in text_attrs).casefold()
        score = 0
        for alternatives in parsed.groups:
            if any(alternative in title for alternative in alternatives):
                score += 25
            elif any(alternative in text for alternative in alternatives):
                score += 5
        if score:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: -pair[0])
    return [item for _, item in ranked]


def search_news(parsed: ParsedQuery, limit: int = 12) -> list[NewsArticle]:
    if not parsed.groups:
        return []
    condition = Q()
    for alternatives in parsed.groups:
        for name in ("title", "summary", "category", "seo_keywords"):
            condition |= _any_of(name, alternatives)
    # body and tags are JSON; compare their text in Python on a bounded set.
    articles = list(
        NewsArticle.objects.filter(
            status=NewsArticle.Status.PUBLISHED,
            published_at__lte=timezone.now(),
        )[:500]
    )
    for article in articles:
        article.search_text = " ".join([article.summary, article.category, article.seo_keywords, str(article.body), " ".join(article.tags or [])])
    return _rank_texts(articles, parsed, "title", ("search_text",))[:limit]


def search_documents(parsed: ParsedQuery, limit: int = 12) -> list[ProductDocument]:
    if not parsed.groups:
        return []
    condition = Q()
    for alternatives in parsed.groups:
        condition |= _any_of("title", alternatives) | _any_of("text_note", alternatives) | _any_of("product__model", alternatives)
    documents = list(
        ProductDocument.objects.filter(
            condition,
            is_active=True,
            product__status=Product.Status.PUBLISHED,
            product__category__is_active=True,
        ).select_related("product")[:200]
    )
    return _rank_texts(documents, parsed, "title", ("text_note",))[:limit]


def search_knowledge(parsed: ParsedQuery, limit: int = 8) -> tuple[list[FAQ], list[KnowledgeEntry]]:
    if not parsed.groups:
        return [], []
    faqs = list(FAQ.objects.filter(is_published=True)[:500])
    entries = list(KnowledgeEntry.objects.filter(is_published=True)[:500])
    return (
        _rank_texts(faqs, parsed, "question", ("answer", "group"))[:limit],
        _rank_texts(entries, parsed, "title", ("body",))[:limit],
    )


SENSITIVE_PATTERN = re.compile(r"@|\d{6,}|https?://", re.IGNORECASE)


def record_search_miss(query: str) -> None:
    """Store a no-result search term unless it looks like contact data."""
    normalized = " ".join(query.casefold().split())[:MAX_QUERY_LENGTH]
    if len(normalized) < 2 or SENSITIVE_PATTERN.search(normalized) or not normalized.isprintable():
        return
    now = timezone.now()
    miss, created = SearchMiss.objects.get_or_create(query=normalized, defaults={"last_seen_at": now})
    if not created:
        from django.db.models import F

        SearchMiss.objects.filter(pk=miss.pk).update(count=F("count") + 1, last_seen_at=now, resolved=False)

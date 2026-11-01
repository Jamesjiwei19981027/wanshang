"""Keep each product's search text in step with everything it is built from."""

from __future__ import annotations

import threading
from contextlib import contextmanager

from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save

from .models import (
    Application,
    Category,
    Product,
    ProductDocument,
    ProductSpecValue,
    SearchSynonym,
    SpecField,
)


_batch = threading.local()


@contextmanager
def batched_search_updates():
    """Collect refreshes while saving many related rows; schedule them once."""
    if getattr(_batch, "ids", None) is not None:
        yield
        return
    _batch.ids = set()
    try:
        yield
    finally:
        ids, _batch.ids = _batch.ids, None
        if ids:
            _schedule(ids)


def _schedule(product_ids) -> None:
    from .search import invalidate_search_caches
    from .specs import refresh_search_document

    ids = sorted({pk for pk in product_ids if pk})
    if getattr(_batch, "ids", None) is not None:
        _batch.ids.update(ids)
        return

    def run() -> None:
        for product_id in ids:
            refresh_search_document(product_id)
        invalidate_search_caches()

    transaction.on_commit(run)


def document_saved(sender, instance: ProductDocument, **kwargs) -> None:
    from .tasks import enqueue_document_text_extraction

    new_name = instance.file.name if instance.file else ""
    if new_name.lower().endswith(".pdf") and new_name != getattr(instance, "_replaced_file_name", None):
        document_id = instance.pk
        transaction.on_commit(lambda: enqueue_document_text_extraction(document_id))


def product_saved(sender, instance: Product, **kwargs) -> None:
    _schedule([instance.pk])


def product_related_changed(sender, instance, **kwargs) -> None:
    _schedule([instance.product_id])


def product_applications_changed(sender, instance, action: str, reverse: bool, pk_set, **kwargs) -> None:
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    if reverse:
        _schedule(pk_set or instance.products.values_list("pk", flat=True))
    else:
        _schedule([instance.pk])


def application_saved(sender, instance: Application, **kwargs) -> None:
    _schedule(instance.products.values_list("pk", flat=True))


def category_saved(sender, instance: Category, **kwargs) -> None:
    if not instance.pk:
        return
    _schedule(Product.objects.filter(category_id__in=instance.descendant_ids()).values_list("pk", flat=True))


def spec_field_saved(sender, instance: SpecField, **kwargs) -> None:
    _schedule(instance.values.values_list("product_id", flat=True))


def synonyms_changed(sender, instance, **kwargs) -> None:
    from .search import invalidate_search_caches

    transaction.on_commit(invalidate_search_caches)


def connect() -> None:
    post_save.connect(product_saved, sender=Product, dispatch_uid="catalog.search.product")
    for model in (ProductSpecValue, ProductDocument):
        post_save.connect(product_related_changed, sender=model, dispatch_uid=f"catalog.search.save.{model.__name__}")
        post_delete.connect(product_related_changed, sender=model, dispatch_uid=f"catalog.search.delete.{model.__name__}")
    post_save.connect(document_saved, sender=ProductDocument, dispatch_uid="catalog.documents.extract_text")
    m2m_changed.connect(
        product_applications_changed,
        sender=Product.applications.through,
        dispatch_uid="catalog.search.applications",
    )
    post_save.connect(application_saved, sender=Application, dispatch_uid="catalog.search.application")
    post_save.connect(category_saved, sender=Category, dispatch_uid="catalog.search.category")
    post_save.connect(spec_field_saved, sender=SpecField, dispatch_uid="catalog.search.spec_field")
    post_save.connect(synonyms_changed, sender=SearchSynonym, dispatch_uid="catalog.search.synonym.save")
    post_delete.connect(synonyms_changed, sender=SearchSynonym, dispatch_uid="catalog.search.synonym.delete")

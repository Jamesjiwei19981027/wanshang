from django.core.management.base import BaseCommand

from catalog.models import Product
from catalog.search import invalidate_search_caches
from catalog.specs import refresh_search_document


class Command(BaseCommand):
    help = "Rebuild every product's search text (run after migrations; changes are otherwise kept in step automatically)."

    def handle(self, *args, **options):
        count = 0
        for product_id in Product.objects.values_list("pk", flat=True).iterator():
            refresh_search_document(product_id)
            count += 1
        invalidate_search_caches()
        self.stdout.write(self.style.SUCCESS(f"Rebuilt search text for {count} product(s)."))

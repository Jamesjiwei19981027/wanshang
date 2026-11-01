from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.company_data import CATEGORIES, PROFILE_DEFAULTS, SITE_DEFAULTS
from catalog.models import Category, CompanyProfile, SiteSetting


class Command(BaseCommand):
    help = "Apply the supplied WANSHANG identity, manufacturer profile and product categories without creating sample products."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Validate the proposed configuration without saving it.")

    @transaction.atomic
    def handle(self, *args, **options):
        for model, values in ((SiteSetting, SITE_DEFAULTS), (CompanyProfile, PROFILE_DEFAULTS)):
            instance = model.objects.filter(pk=1).first() or model(pk=1)
            for field, value in values.items():
                setattr(instance, field, value)
            instance.full_clean()
            if not options["dry_run"]:
                instance.save()

        for position, (name, slug, description, icon) in enumerate(CATEGORIES):
            category = Category.objects.filter(slug=slug).first() or Category(slug=slug)
            category.name = name
            category.description = description
            category.icon = icon
            category.position = position
            category.is_active = True
            category.full_clean()
            if not options["dry_run"]:
                category.save()

        verb = "Validated" if options["dry_run"] else "Applied"
        self.stdout.write(self.style.SUCCESS(f"{verb} WANSHANG company settings, published manufacturer profile and six product categories. No products or specifications were created."))

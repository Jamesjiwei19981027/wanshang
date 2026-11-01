from django.core.management.base import BaseCommand
from django.db import transaction

from assistant.models import AssistantSetting, QuickPrompt
from catalog.company_data import CATEGORIES, PROFILE_DEFAULTS, QUICK_PROMPTS, SITE_DEFAULTS
from catalog.models import Category, CompanyProfile, SiteSetting
from catalog.spec_templates import CATEGORY_TEMPLATES, apply_template


class Command(BaseCommand):
    help = (
        "Apply the supplied WANSHANG identity, manufacturer profile, six product categories, "
        "the trailer-axle parameter list and assistant defaults, without creating sample products."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Validate the proposed configuration without saving it.")

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        for model, values in ((SiteSetting, SITE_DEFAULTS), (CompanyProfile, PROFILE_DEFAULTS)):
            instance = model.objects.filter(pk=1).first() or model(pk=1)
            for field, value in values.items():
                setattr(instance, field, value)
            instance.full_clean()
            if not dry_run:
                instance.save()

        created_parameters = 0
        for position, (name, slug, description, icon) in enumerate(CATEGORIES):
            category = Category.objects.filter(slug=slug).first() or Category(slug=slug)
            category.name = name
            category.description = description
            category.icon = icon
            category.position = position
            category.is_active = True
            category.full_clean()
            if dry_run:
                continue
            category.save()
            if slug in CATEGORY_TEMPLATES:
                created_parameters += apply_template(category, CATEGORY_TEMPLATES[slug])

        if not dry_run:
            if not AssistantSetting.objects.filter(pk=1).exists():
                AssistantSetting.load().save()
            if not QuickPrompt.objects.exists():
                for position, (page, text) in enumerate(QUICK_PROMPTS):
                    QuickPrompt.objects.create(page=page, text=text, position=position)

        verb = "Validated" if dry_run else "Applied"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} WANSHANG company settings, published manufacturer profile and six product categories; "
                f"{created_parameters} new trailer-axle parameter(s). No products were created."
            )
        )

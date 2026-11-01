from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Application, Category, Product, ProductSpecValue, SpecField
from catalog.spec_templates import TRAILER_AXLE_PARAMETERS, apply_template

DEMO_CATEGORY_SLUG = "demo-trailer-axles"

# Values are entered as staff would: some in lb/in, some in kg/mm.
DEMO_PRODUCTS = (
    {
        "title": "3,500 lb Electric Brake Straight Axle", "slug": "demo-ta-3500-eb", "model": "DEMO-TA-3500-EB", "sku": "DEMO-AX-35-EB",
        "short_description": "Demonstration record: configurable 3,500 lb straight axle with electric drum brakes.",
        "applications": ["demo-utility-trailers", "demo-car-haulers"],
        "specs": {"rated_capacity": ("3500", "lb"), "axle_type": "Straight", "brake_type": "Electric drum", "bolt_pattern": "5 on 4.5", "hub_face": ("95", "in"), "spring_center": ("80", "in"), "tube_shape": "Round", "tube_size": '2-3/8" round', "surface_finish": "E-coated", "brake_voltage": ("12", "v")},
    },
    {
        "title": '7,000 lb Hydraulic Disc 4" Drop Axle', "slug": "demo-ta-7000-hd", "model": "DEMO-TA-7000-HD", "sku": "DEMO-AX-70-HD",
        "short_description": "Demonstration record: heavy-duty drop axle with hydraulic disc brakes and an 8-lug hub.",
        "applications": ["demo-car-haulers"],
        "specs": {"rated_capacity": ("3175", "kg"), "axle_type": "Drop", "brake_type": "Hydraulic disc", "bolt_pattern": "8 on 6.5", "hub_face": ("2413", "mm"), "spring_center": ("2032", "mm"), "drop_height": ("4", "in"), "surface_finish": "Powder-coated"},
    },
    {
        "title": "2,200 lb Galvanised Idler Boat Axle", "slug": "demo-ta-2200-id", "model": "DEMO-TA-2200-ID", "sku": "DEMO-AX-22-ID",
        "short_description": "Demonstration record: no-brake galvanised axle for boat trailers.",
        "applications": ["demo-boat-trailers"],
        "specs": {"rated_capacity": ("2200", "lb"), "axle_type": "Straight", "brake_type": "No brake", "bolt_pattern": "5 on 4.5", "hub_face": ("89", "in"), "spring_center": ("74", "in"), "surface_finish": "Hot-dip galvanized"},
    },
)
DEMO_APPLICATIONS = (
    ("Demo: Utility Trailers", "demo-utility-trailers"),
    ("Demo: Car Haulers", "demo-car-haulers"),
    ("Demo: Boat Trailers", "demo-boat-trailers"),
)


class Command(BaseCommand):
    help = "Create or update a small demonstration catalogue in its own category, for testing only."

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.DJANGO_ENV == "production":
            raise CommandError("seed_demo is disabled in production environments.")

        category, _ = Category.objects.update_or_create(
            slug=DEMO_CATEGORY_SLUG,
            defaults={"name": "Demo: Trailer Axles", "description": "Demonstration data for testing. Not part of the real catalogue.", "icon": "◉", "position": 900, "is_active": True, "parent": None},
        )
        apply_template(category, TRAILER_AXLE_PARAMETERS)
        fields = {field.key: field for field in category.spec_fields.all()}
        applications = {}
        for position, (name, slug) in enumerate(DEMO_APPLICATIONS):
            applications[slug], _ = Application.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "summary": "Demonstration application.", "position": 900 + position, "is_active": True},
            )

        for position, data in enumerate(DEMO_PRODUCTS):
            product, _ = Product.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "category": category, "title": data["title"], "model": data["model"], "sku": data["sku"],
                    "short_description": data["short_description"],
                    "description": "Demonstration data. Replace with verified company material before launch.",
                    "is_customizable": True, "oem_available": True, "made_to_order": True,
                    "featured": position < 3, "status": Product.Status.PUBLISHED, "position": 900 + position,
                },
            )
            product.applications.set(applications[slug] for slug in data["applications"])
            for key, value in data["specs"].items():
                field = fields[key]
                spec, _ = ProductSpecValue.objects.get_or_create(product=product, field=field)
                if field.data_type == SpecField.DataType.NUMBER:
                    spec.number_value, spec.entered_unit = Decimal(value[0]), value[1]
                else:
                    spec.text_value = value
                spec.full_clean()
                spec.save()
        self.stdout.write(self.style.SUCCESS("Demo catalogue is ready under 'Demo: Trailer Axles'. Delete that category's products before launch."))

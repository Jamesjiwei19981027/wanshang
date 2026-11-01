from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.models import Application, AxleSpec, Category, Product


class Command(BaseCommand):
    help = "Create or update a small, clearly labelled demonstration catalogue."

    def handle(self, *args, **options):
        if settings.DJANGO_ENV == "production":
            raise CommandError("seed_demo is disabled in production environments.")

        categories = [
            ("Trailer Axles", "trailer-axles", "Straight, drop and torsion axle assemblies engineered around your geometry.", "◉"),
            ("Brake Assemblies", "brake-assemblies", "Electric, hydraulic drum and disc braking systems.", "◌"),
            ("Hubs & Drums", "hubs-drums", "Machined hub, drum and bearing combinations.", "⊙"),
            ("Suspension Parts", "suspension-parts", "Equalizers, hangers, shackles and heavy-duty kits.", "⌁"),
            ("Leaf Springs", "leaf-springs", "Single-eye, double-eye and custom spring packs.", "≋"),
            ("Custom Components", "custom-trailer-components", "Drawing-led OEM and made-to-order trailer components.", "✦"),
        ]
        category_by_slug = {}
        for position, (name, slug, description, icon) in enumerate(categories):
            category, _ = Category.objects.update_or_create(slug=slug, defaults={"name": name, "description": description, "icon": icon, "position": position, "is_active": True})
            category_by_slug[slug] = category

        application_data = [
            ("Utility Trailers", "utility-trailers", "Balanced running gear for everyday utility and landscaping trailers.", "E-coat or powder coat"),
            ("Boat Trailers", "boat-trailers", "Corrosion-conscious components for water exposure.", "Hot-dip galvanised"),
            ("Agricultural Trailers", "agricultural-trailers", "Axle configurations for uneven terrain and variable loads.", "Heavy-duty paint"),
            ("Car Haulers", "car-haulers", "Brake-ready assemblies for controlled vehicle transport.", "Powder coat"),
            ("RV & Caravan", "rv-caravan", "Compact running gear selected for travel and serviceability.", "E-coat"),
            ("Construction Trailers", "construction-trailers", "Robust running gear direction for tools, equipment and repeated jobsite duty.", "Heavy-duty paint"),
            ("Heavy-Duty Transport", "heavy-duty-transport", "Robust assemblies for demanding duty cycles.", "Custom protection"),
        ]
        application_by_slug = {}
        for position, (name, slug, summary, finish) in enumerate(application_data):
            application, _ = Application.objects.update_or_create(slug=slug, defaults={"name": name, "summary": summary, "recommended_finish": finish, "position": position, "is_active": True})
            application_by_slug[slug] = application

        # The frontend maps blank demo assets to committed SVG illustrations.
        # Production products should instead use verified company image URLs.
        photo = ""
        boat_photo = ""
        products = [
            {"title": "3,500 lb Electric Brake Straight Axle", "slug": "ta-3500-electric-brake-straight-axle", "model": "TA-3500-EB", "sku": "AX-35-EB-S", "short_description": "A configurable 3,500 lb axle assembly with electric drum brakes for utility and car-hauler builds.", "capacity": 3500, "type": "straight", "brake": "Electric Drum", "bolt": "5 on 4.5", "tube": '2-3/8" round', "hub_face": 95, "spring": 80, "finish": "E-Coated", "image": photo, "applications": ["utility-trailers", "car-haulers"], "spec": {"overall_length": 97, "tube_shape": "Round", "tube_wall_thickness": "0.188 in", "hub_type": "Hub & drum", "spindle_type": "#84", "inner_bearing": "L68149", "outer_bearing": "L44649", "brake_size": '10" × 2-1/4"', "brake_voltage": "12 V", "corrosion_protection": "E-coated", "moq": "Discuss with sales", "lead_time": "Confirmed per configuration"}},
            {"title": '7,000 lb Hydraulic Disc 4" Drop Axle', "slug": "ta-7000-hydraulic-disc-drop-axle", "model": "TA-7000-HD", "sku": "AX-70-HD-D4", "short_description": "A heavy-duty drop axle platform with hydraulic disc braking and an 8-lug hub configuration.", "capacity": 7000, "type": "drop", "brake": "Hydraulic Disc", "bolt": "8 on 6.5", "tube": '3" round', "hub_face": 95, "spring": 80, "finish": "Powder-Coated", "image": photo, "applications": ["car-haulers", "heavy-duty-transport"], "spec": {"overall_length": 97, "tube_shape": "Round", "drop_height": '4"', "hub_type": "Disc hub", "spindle_type": "#42", "brake_size": '12" disc', "corrosion_protection": "Powder-coated", "moq": "Discuss with sales", "lead_time": "Confirmed per configuration"}},
            {"title": "2,200 lb Galvanised Idler Boat Axle", "slug": "ta-2200-galvanised-idler-boat-axle", "model": "TA-2200-M", "sku": "AX-22-ID-G", "short_description": "A no-brake galvanised axle option for boat trailer applications where corrosion resistance matters.", "capacity": 2200, "type": "straight", "brake": "No Brake", "bolt": "5 on 4.5", "tube": '2-3/8" round', "hub_face": 89, "spring": 74, "finish": "Hot-Dip Galvanized", "image": boat_photo, "applications": ["boat-trailers"], "spec": {"tube_shape": "Round", "hub_type": "Idler hub", "spindle_type": "#84", "corrosion_protection": "Hot-dip galvanized", "moq": "Discuss with sales", "lead_time": "Confirmed per configuration"}},
            {"title": "5,200 lb Straight Electric Brake Axle", "slug": "ta-5200-electric-brake-straight-axle", "model": "TA-5200-EB", "sku": "AX-52-EB-S", "short_description": "A configurable 5,200 lb brake axle for car haulers and utility trailers.", "capacity": 5200, "type": "straight", "brake": "Electric Drum", "bolt": "6 on 5.5", "tube": '3" round', "hub_face": 95, "spring": 80, "finish": "E-Coated", "image": photo, "applications": ["utility-trailers", "car-haulers"], "spec": {"tube_shape": "Round", "hub_type": "Hub & drum", "brake_voltage": "12 V", "corrosion_protection": "E-coated", "moq": "Discuss with sales", "lead_time": "Confirmed per configuration"}},
        ]
        for position, data in enumerate(products):
            product, _ = Product.objects.update_or_create(
                slug=data["slug"],
                defaults={
                    "category": category_by_slug["trailer-axles"], "title": data["title"], "model": data["model"], "sku": data["sku"], "short_description": data["short_description"],
                    "description": "Starter catalogue data. Confirm all dimensions, materials and commercial details before publishing.", "image_url": data["image"], "rated_capacity": data["capacity"], "axle_type": data["type"], "brake_type": data["brake"], "bolt_pattern": data["bolt"], "tube_size": data["tube"], "hub_face_length": data["hub_face"], "spring_center": data["spring"], "surface_finish": data["finish"], "is_customizable": True, "oem_available": True, "made_to_order": True, "featured": position < 3, "status": Product.Status.PUBLISHED, "position": position,
                },
            )
            product.applications.set(application_by_slug[slug] for slug in data["applications"])
            AxleSpec.objects.update_or_create(product=product, defaults=data["spec"])
        self.stdout.write(self.style.SUCCESS("Demo catalogue is ready. All content is clearly starter data; replace it with verified company material."))

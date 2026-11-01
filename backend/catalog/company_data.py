"""Company-approved identity and product families supplied on 31 October 2026.

Markets are proposed enquiry destinations, not verified export history. No
catalogue model numbers, load ratings or certifications were supplied.
"""

SITE_DEFAULTS = {
    "site_name": "WANSHANG",
    "legal_name": "Shandong Wanshang International Trading Co., Ltd.",
    "chinese_name": "山东万商国际商贸有限公司",
    "tagline": "Trailer Axles & Suspension Systems",
    "contact_person": "Si Mingzhu (司明珠)",
    "contact_title": "Chairperson",
    "contact_email": "simingzhu727@gmail.com",
    "sales_email": "simingzhu727@gmail.com",
    "phone_number": "+86 15253754552",
    "website_url": "https://www.wan-shang.com",
    "address": "Liangshan Subdistrict, Liangshan County, Shandong Province, China",
    "global_seo_title": "WANSHANG | Trailer Axle & Suspension Manufacturer",
    "global_seo_keywords": "trailer axle manufacturer, American type axle, German type axle, trailer suspension, landing gear, Wanshang",
    "global_seo_description": "Shandong Wanshang International Trading Co., Ltd. manufactures trailer axles, suspension systems and related components in Liangshan, Shandong, China.",
    "footer_text": "A manufacturer of trailer axles, suspension systems and related components based in Liangshan, Shandong, China.",
}

AXLE_FAMILIES = (
    "American-Type Axles", "American-Type Drum Brake Axles",
    "American-Type Disc Brake Axles", "German-Type Axles",
    "American-Type Single-Tyre Axles", "Lightweight Axles",
    "Spoke-Wheel Axles", "Heavy-Duty Axles", "Unbraked Axles",
    "Disc Brake Axles", "Special Axles",
)
SUSPENSION_FAMILIES = (
    "Mechanical Suspension", "Air Suspension", "Rigid Suspension",
    "Single-Point Suspension",
)
OTHER_FAMILIES = ("Wheel Hubs", "Brake Drums", "Automotive Parts", "Trailer Landing Gear")

PROFILE_DEFAULTS = {
    "headline": "Trailer axles and suspension systems, manufactured in Liangshan.",
    "summary": "WANSHANG is a manufacturer of trailer axles, suspension systems and related components in Shandong, China, welcoming enquiries from trailer builders, importers, distributors and professional parts buyers.",
    "business_role": "manufacturing",
    "introduction": "Shandong Wanshang International Trading Co., Ltd. (山东万商国际商贸有限公司) is a manufacturer based in Liangshan Subdistrict, Liangshan County, Shandong Province, China. Our product range covers trailer axles, suspension systems, wheel hubs, brake drums, automotive parts and trailer landing gear.\n\nWe welcome enquiries from trailer manufacturers, importers, distributors, wholesalers, fleet operators and repair businesses. Send your required product type, dimensions, operating conditions, quantity and destination so we can discuss a suitable configuration and quotation.",
    "main_products": (
        "Trailer axles: " + ", ".join(AXLE_FAMILIES) + ".\n\n"
        "Suspension systems: " + ", ".join(SUSPENSION_FAMILIES) + ".\n\n"
        "Other components: " + ", ".join(OTHER_FAMILIES) + "."
    ),
    "markets": "Our priority target markets are Africa, the Middle East and Southeast Asia. Central Asia and Latin America are additional regions for market development. We welcome enquiries from trailer manufacturers, local importers, distributors and aftermarket buyers in these regions.\n\nShare your destination country, trailer application, road conditions and required specifications so we can review the configuration and documentation needed for your project.",
    "meta_title": "About us | Trailer Axle & Suspension Manufacturer",
    "meta_description": "Meet Shandong Wanshang International Trading Co., Ltd., a manufacturer of trailer axles, suspension systems and related components based in Liangshan, China.",
    "is_published": True,
}

# The current CMS has flat categories. The three source catalogue groups are
# represented by axles, suspension and the four individual accessory families.
CATEGORIES = (
    ("Trailer Axles", "trailer-axles", ", ".join(AXLE_FAMILIES) + ".", "◉"),
    ("Suspension Systems", "suspension-parts", ", ".join(SUSPENSION_FAMILIES) + ".", "⌁"),
    ("Wheel Hubs", "wheel-hubs", "Wheel hubs for trailer axle assemblies. Confirm fitment against the axle and wheel specifications.", "⊙"),
    ("Brake Drums", "brake-drums", "Trailer brake drums. Send the part reference, dimensions and axle configuration for matching.", "◌"),
    ("Automotive Parts", "automotive-parts", "Automotive replacement parts. Share the vehicle model, part number or drawing with your enquiry.", "⚙"),
    ("Trailer Landing Gear", "landing-gear", "Trailer support legs and landing gear. Confirm mounting dimensions and required configuration with your enquiry.", "⌑"),
)

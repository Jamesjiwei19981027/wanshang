"""Move the FAQ that was written into the website into the admin-managed list."""

from django.db import migrations

FAQ_ROWS = (
    ("Product selection", "How do I choose a trailer axle from this catalogue?", "Start with the trailer application and actual gross weight, then filter by rated capacity, axle direction (straight or drop), brake type and bolt pattern. Compare hub face and spring centre on product pages. Submit the complete dimension set through RFQ so engineering can confirm the configuration against your trailer geometry."),
    ("Product selection", "Are published dimensions a final engineering approval?", "No. Catalogue values are published configuration guidance. Final axle selection requires complete trailer geometry, actual load distribution, wheels and tyres, mounting interfaces, braking requirements, operating environment and destination-market regulations."),
    ("Product selection", "What do hub face and spring centre mean?", "Hub face is the distance between the wheel-mounting surfaces of the hubs on opposite ends of the axle. Spring centre is the distance between the centre lines of the two spring seats. Confirm both measurements against the trailer frame, suspension, wheels and required clearances. If either differs from a listed platform, request a configuration review before order release."),
    ("RFQ and orders", "What should I include in an RFQ?", "Include intended application, gross weight and load distribution, hub face, spring centre, bolt pattern, stud size, brake type, surface finish, destination market and quantity. Attach drawings or photos when available. Add model or SKU when one is already selected."),
    ("RFQ and orders", "Can I request a custom or OEM configuration?", "Yes. Mark custom requirements in the RFQ and provide target dimensions or a drawing. Custom, made-to-order and OEM options depend on the platform, volume and technical review; they are not confirmed by the public catalogue alone."),
    ("RFQ and orders", "Are technical drawings available?", "Published documents appear on product pages and the download centre when released. Controlled or configuration-specific approval drawings are issued through the RFQ workflow after technical review."),
    ("Quality, packing and delivery", "How are quality and inspection claims handled?", "The site publishes only company-verified test and certification evidence. Final inspection points, traceability, packaging, lead time and loading port are confirmed against the approved configuration and order."),
    ("Quality, packing and delivery", "Which markets and regulations apply?", "Requirements differ by destination market. State the destination and any applicable brake, lighting or marking rules in the RFQ so the configuration can be reviewed for that market."),
)


def add_existing_faqs(apps, schema_editor):
    FAQ = apps.get_model("catalog", "FAQ")
    if FAQ.objects.exists():
        return
    for position, (group, question, answer) in enumerate(FAQ_ROWS):
        FAQ.objects.create(group=group, question=question, answer=answer, position=position * 10, is_published=True)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0022_generic_parameters_search_knowledge")]

    operations = [migrations.RunPython(add_existing_faqs, migrations.RunPython.noop)]

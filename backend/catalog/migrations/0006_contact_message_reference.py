from django.db import migrations, models


def populate_contact_references(apps, schema_editor):
    ContactMessage = apps.get_model("catalog", "ContactMessage")
    for message in ContactMessage.objects.filter(reference__isnull=True).iterator():
        stamp = message.created_at.strftime("%y%m%d")
        message.reference = f"MSG-{stamp}-{message.pk:08X}"
        message.save(update_fields=["reference"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_sessions_attribution_followups_and_units"),
    ]

    operations = [
        migrations.AddField(
            model_name="contactmessage",
            name="reference",
            field=models.CharField(
                editable=False,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(
            populate_contact_references,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="contactmessage",
            name="reference",
            field=models.CharField(
                editable=False,
                max_length=32,
                unique=True,
            ),
        ),
    ]

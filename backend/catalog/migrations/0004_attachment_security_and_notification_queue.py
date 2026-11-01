from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_notification_delivery_state")]

    operations = [
        migrations.AddField(
            model_name="inquiry",
            name="notification_queued_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="notification_queued_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="inquiryattachment",
            name="declared_mime_type",
            field=models.CharField(blank=True, editable=False, max_length=120),
        ),
        migrations.AddField(
            model_name="inquiryattachment",
            name="scan_detail",
            field=models.CharField(blank=True, editable=False, max_length=500),
        ),
        migrations.AddField(
            model_name="inquiryattachment",
            name="scan_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending scan"),
                    ("clean", "Clean"),
                    ("infected", "Infected"),
                    ("error", "Scan error"),
                ],
                db_index=True,
                default="pending",
                editable=False,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="inquiryattachment",
            name="scanned_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]

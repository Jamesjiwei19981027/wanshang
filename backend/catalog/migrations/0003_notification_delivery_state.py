from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_private_attachments_and_source_urls")]

    operations = [
        migrations.AddField(
            model_name="inquiry",
            name="notification_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="confirmation_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="notification_error",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="notification_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="confirmation_sent_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="notification_error",
            field=models.TextField(blank=True, editable=False),
        ),
    ]

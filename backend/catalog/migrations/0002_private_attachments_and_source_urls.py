import shutil
from pathlib import Path

import catalog.models
import catalog.storage
from django.conf import settings
from django.db import migrations, models


def copy_existing_attachments_to_private_storage(apps, schema_editor):
    InquiryAttachment = apps.get_model("catalog", "InquiryAttachment")
    for attachment in InquiryAttachment.objects.iterator():
        relative_name = attachment.file.name
        if not relative_name:
            continue
        old_path = Path(settings.MEDIA_ROOT) / relative_name
        new_path = Path(settings.PRIVATE_MEDIA_ROOT) / relative_name
        if old_path.is_file() and not new_path.exists():
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_path, new_path)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="inquiry",
            name="source_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name="contactmessage",
            name="source_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name="inquiryattachment",
            name="file",
            field=models.FileField(
                storage=catalog.storage.PrivateMediaStorage(),
                upload_to=catalog.models.inquiry_attachment_path,
            ),
        ),
        migrations.RunPython(copy_existing_attachments_to_private_storage, migrations.RunPython.noop),
    ]

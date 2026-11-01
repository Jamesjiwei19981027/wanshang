from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0016_remove_public_inquiry_attachment_copies"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="ResourceArticle",
            new_name="NewsArticle",
        ),
        migrations.AlterField(
            model_name="newsarticle",
            name="cover_image",
            field=models.ImageField(blank=True, upload_to="news/%Y/%m/"),
        ),
    ]

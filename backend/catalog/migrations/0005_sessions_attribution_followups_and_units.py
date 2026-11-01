import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_attachment_security_and_notification_queue"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="contactmessage",
            name="first_landing_page",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="first_touch_source",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="ip_hash",
            field=models.CharField(blank=True, editable=False, max_length=128),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="last_landing_page",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="last_touch_source",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="referrer",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="user_agent",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="utm_campaign",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="utm_content",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="utm_medium",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="utm_term",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="admin_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="first_landing_page",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="first_touch_source",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="last_landing_page",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="last_touch_source",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="next_followup_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="user_agent",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="utm_content",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="utm_term",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="product",
            name="capacity_unit",
            field=models.CharField(
                choices=[("lb", "Pounds (lb)"), ("kg", "Kilograms (kg)")],
                default="lb",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="product",
            name="dimension_unit",
            field=models.CharField(
                choices=[("in", "Inches (in)"), ("mm", "Millimetres (mm)")],
                default="in",
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="analyticsevent",
            name="path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AlterField(
            model_name="analyticsevent",
            name="session_id",
            field=models.CharField(blank=True, db_index=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="inquiry",
            name="utm_campaign",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AlterField(
            model_name="product",
            name="rated_capacity",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="InquiryFollowup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "action_type",
                    models.CharField(
                        choices=[
                            ("email", "Email"),
                            ("call", "Call"),
                            ("whatsapp", "WhatsApp"),
                            ("quote", "Quote"),
                            ("meeting", "Meeting"),
                            ("note", "Internal note"),
                        ],
                        default="note",
                        max_length=30,
                    ),
                ),
                ("content", models.TextField()),
                ("next_followup_at", models.DateTimeField(blank=True, null=True)),
                (
                    "inquiry",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="followups",
                        to="catalog.inquiry",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inquiry_followups",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="VisitSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("session_key", models.CharField(max_length=100, unique=True)),
                ("first_ip_hash", models.CharField(blank=True, editable=False, max_length=128)),
                ("country", models.CharField(blank=True, max_length=100)),
                ("city", models.CharField(blank=True, max_length=100)),
                ("first_touch_source", models.CharField(blank=True, max_length=120)),
                ("last_touch_source", models.CharField(blank=True, max_length=120)),
                ("utm_source", models.CharField(blank=True, max_length=120)),
                ("utm_medium", models.CharField(blank=True, max_length=120)),
                ("utm_campaign", models.CharField(blank=True, max_length=150)),
                ("utm_term", models.CharField(blank=True, max_length=150)),
                ("utm_content", models.CharField(blank=True, max_length=150)),
                ("referrer_url", models.TextField(blank=True)),
                ("first_landing_page", models.CharField(blank=True, max_length=500)),
                ("last_landing_page", models.CharField(blank=True, max_length=500)),
                ("user_agent", models.TextField(blank=True, editable=False)),
                ("device_type", models.CharField(blank=True, max_length=50)),
                ("browser", models.CharField(blank=True, max_length=100)),
                ("os", models.CharField(blank=True, max_length=100)),
                (
                    "consent_status",
                    models.CharField(
                        choices=[
                            ("unknown", "Unknown"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                        ],
                        default="unknown",
                        max_length=30,
                    ),
                ),
                ("started_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("last_seen_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("converted_at", models.DateTimeField(blank=True, editable=False, null=True)),
            ],
            options={
                "ordering": ("-last_seen_at",),
                "indexes": [
                    models.Index(fields=["last_seen_at"], name="catalog_vis_last_se_6f033d_idx"),
                    models.Index(fields=["first_touch_source", "created_at"], name="catalog_vis_first_t_f509cb_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="analyticsevent",
            name="visit_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="events",
                to="catalog.visitsession",
            ),
        ),
        migrations.AddField(
            model_name="contactmessage",
            name="session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contact_messages",
                to="catalog.visitsession",
            ),
        ),
        migrations.AddField(
            model_name="inquiry",
            name="session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="inquiries",
                to="catalog.visitsession",
            ),
        ),
        migrations.CreateModel(
            name="PageView",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("path", models.CharField(max_length=500)),
                ("page_title", models.CharField(blank=True, max_length=255)),
                ("timestamp", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "product",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="page_views",
                        to="catalog.product",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="page_views",
                        to="catalog.visitsession",
                    ),
                ),
            ],
            options={
                "ordering": ("-timestamp",),
                "indexes": [
                    models.Index(fields=["session", "timestamp"], name="catalog_pag_session_bf7ef6_idx"),
                    models.Index(fields=["path", "timestamp"], name="catalog_pag_path_8888a7_idx"),
                ],
            },
        ),
    ]

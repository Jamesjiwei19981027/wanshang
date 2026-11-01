from decimal import Decimal

from django.db import migrations, models


def copy_settings_into_provider(apps, schema_editor):
    """Keep the model name and prices staff already entered as a DeepSeek
    provider without a key; staff add the key in the admin."""
    AssistantSetting = apps.get_model("assistant", "AssistantSetting")
    AIProvider = apps.get_model("assistant", "AIProvider")
    setting = AssistantSetting.objects.filter(pk=1).first()
    if setting is None:
        return
    AIProvider.objects.create(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        model_name=setting.model_name or "deepseek-chat",
        priority=10,
        is_active=True,
        price_input_cache_hit=setting.price_input_cache_hit,
        price_input_cache_miss=setting.price_input_cache_miss,
        price_output=setting.price_output,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("assistant", "0001_generic_parameters_search_knowledge"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIProvider",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="For staff, e.g. DeepSeek or Qwen.", max_length=80)),
                (
                    "base_url",
                    models.CharField(
                        max_length=300,
                        default="https://api.deepseek.com",
                        help_text="OpenAI-compatible address without /chat/completions, e.g. https://api.deepseek.com.",
                        verbose_name="API address",
                    ),
                ),
                ("model_name", models.CharField(default="deepseek-chat", max_length=120)),
                ("encrypted_api_key", models.TextField(blank=True, editable=False)),
                ("priority", models.PositiveSmallIntegerField(default=10, help_text="Lower numbers are tried first.")),
                ("is_active", models.BooleanField(default=True)),
                (
                    "extra_parameters",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text=(
                            "Optional JSON merged into every request for this model; null removes a default. "
                            'Example for OpenAI reasoning models: {"max_tokens": null, "max_completion_tokens": 1200, "temperature": null}.'
                        ),
                        verbose_name="Extra request parameters",
                    ),
                ),
                (
                    "price_input_cache_hit",
                    models.DecimalField(
                        decimal_places=4, default=Decimal("0"), max_digits=10,
                        verbose_name="Price per million input tokens (cache hit)",
                    ),
                ),
                (
                    "price_input_cache_miss",
                    models.DecimalField(
                        decimal_places=4, default=Decimal("0"), max_digits=10,
                        verbose_name="Price per million input tokens (cache miss)",
                    ),
                ),
                (
                    "price_output",
                    models.DecimalField(
                        decimal_places=4, default=Decimal("0"), max_digits=10, verbose_name="Price per million output tokens",
                    ),
                ),
                ("last_success_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("last_failure_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("last_error", models.CharField(blank=True, editable=False, max_length=300)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "AI provider", "ordering": ("priority", "id")},
        ),
        migrations.AddField(
            model_name="message",
            name="provider_name",
            field=models.CharField(blank=True, help_text="AI provider(s) that produced this answer.", max_length=200),
        ),
        migrations.RunPython(copy_settings_into_provider, migrations.RunPython.noop),
        migrations.RemoveField(model_name="assistantsetting", name="model_name"),
        migrations.RemoveField(model_name="assistantsetting", name="price_input_cache_hit"),
        migrations.RemoveField(model_name="assistantsetting", name="price_input_cache_miss"),
        migrations.RemoveField(model_name="assistantsetting", name="price_output"),
        migrations.AlterField(
            model_name="assistantsetting",
            name="currency",
            field=models.CharField(default="CNY", help_text="Enter every AI provider's prices in this currency.", max_length=3),
        ),
    ]

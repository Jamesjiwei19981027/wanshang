from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import migrations


PUBLIC_EXTERNAL_URL_FIELDS = (
    ("Application", "image_url"),
    ("Banner", "desktop_image_url"),
    ("Banner", "mobile_image_url"),
    ("Category", "image_url"),
    ("Product", "image_url"),
    ("ProductDocument", "external_url"),
    ("ProductImage", "external_url"),
    ("NewsArticle", "cover_image_url"),
    ("SiteSetting", "default_og_image_url"),
    ("SiteSetting", "site_logo_url"),
    ("SiteSetting", "whatsapp_link"),
)
PUBLIC_HOST_LABEL = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
NUMERIC_IPV4_HOST = re.compile(
    r"^(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*$",
    re.IGNORECASE,
)


def is_public_hostname(hostname):
    candidate = hostname.strip().lower().rstrip(".")
    if (
        not candidate
        or candidate.startswith(".")
        or candidate == "localhost"
        or candidate.endswith(
            (".localhost", ".local", ".internal", ".test", ".invalid")
        )
    ):
        return False
    try:
        return ip_address(candidate.strip("[]")).is_global
    except ValueError:
        if NUMERIC_IPV4_HOST.fullmatch(candidate):
            return False
        labels = candidate.split(".")
        return (
            len(candidate) <= 253
            and len(labels) >= 2
            and all(PUBLIC_HOST_LABEL.fullmatch(label) for label in labels)
        )


def revalidate_existing_public_urls(apps, schema_editor):
    https_validator = URLValidator(schemes=("https",))
    invalid_locations = []
    for model_name, field_name in PUBLIC_EXTERNAL_URL_FIELDS:
        model = apps.get_model("catalog", model_name)
        records = model.objects.exclude(**{field_name: ""}).values_list(
            "pk",
            field_name,
        )
        for pk, value in records.iterator():
            try:
                https_validator(value)
                parsed = urlsplit(value)
                if (
                    parsed.scheme.lower() != "https"
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or "@" in parsed.netloc
                    or not is_public_hostname(parsed.hostname)
                ):
                    raise ValidationError("Unsafe external URL")
            except (TypeError, ValueError, ValidationError):
                invalid_locations.append(f"{model_name}.{field_name} pk={pk}")

    if invalid_locations:
        locations = ", ".join(invalid_locations[:20])
        remainder = len(invalid_locations) - 20
        if remainder > 0:
            locations += f", and {remainder} more"
        raise RuntimeError(
            "Public external URLs must use a publicly routable, "
            "credential-free HTTPS host. Fix these records before retrying "
            f"the migration: {locations}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0017_rename_resource_articles_to_news"),
    ]

    operations = [
        migrations.RunPython(
            revalidate_existing_public_urls,
            migrations.RunPython.noop,
        ),
    ]

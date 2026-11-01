"""Category parameter lists: display, filtering and search text."""

from __future__ import annotations

import math
import re
from decimal import Decimal

from django.db.models import Exists, OuterRef, QuerySet
from rest_framework.exceptions import ValidationError

from .models import Category, Product, ProductSpecValue, SpecField
from .units import (
    IMPERIAL,
    converted_places,
    METRIC,
    QUANTITIES,
    display_unit,
    format_number,
    format_quantity,
    from_base,
    parse_number,
    parse_value_with_unit,
    to_base,
)

SPEC_PARAM_PATTERN = re.compile(r"^spec\.(?P<key>[a-z0-9_-]{1,60}?)(?:__(?P<op>min|max|unit))?$")
MAX_SPEC_FILTERS = 12
# Values entered in lb and converted to kg (or in and mm) rarely land exactly
# on the requested number; treat 1% as the same rating.
NUMBER_MATCH_TOLERANCE = Decimal("0.01")
# Range ends only absorb conversion rounding.
RANGE_TOLERANCE = Decimal("0.001")


def field_label(field: SpecField, language: str = "") -> str:
    if language and language != "en":
        translated = (field.translations or {}).get(language, {}).get("label")
        if translated:
            return translated
    return field.label


def format_spec_value(value: ProductSpecValue, system: str) -> str:
    field = value.field
    if field.data_type == SpecField.DataType.NUMBER:
        if value.number_value is None:
            return ""
        if not field.quantity:
            return format_number(value.number_value, 6)
        target = display_unit(field.quantity, system, field.imperial_unit, field.metric_unit)
        if target == value.entered_unit:
            # Show what staff entered, exactly.
            return format_quantity(value.number_value, field.quantity, target)
        converted = from_base(value.base_value, field.quantity, target)
        return format_quantity(converted, field.quantity, target, places=converted_places(converted))
    if field.data_type == SpecField.DataType.BOOLEAN:
        if value.boolean_value is None:
            return ""
        return "Yes" if value.boolean_value else "No"
    return value.text_value


def serialize_spec_values(product: Product, values: list[ProductSpecValue] | None = None) -> list[dict]:
    """Specs in parameter-list order, displayed in both unit systems."""
    if values is None:
        values = list(product.spec_values.select_related("field"))
    by_field = {value.field_id: value for value in values}
    rows: list[dict] = []
    for field in product.category.effective_spec_fields(products_only=True):
        value = by_field.get(field.pk)
        if value is None or value.is_empty:
            continue
        rows.append(
            {
                "key": field.key,
                "label": field.label,
                "type": field.data_type,
                "quantity": field.quantity,
                "display": {
                    IMPERIAL: format_spec_value(value, IMPERIAL),
                    METRIC: format_spec_value(value, METRIC),
                },
                "value": (
                    float(value.number_value) if value.number_value is not None
                    else value.boolean_value if field.data_type == SpecField.DataType.BOOLEAN
                    else value.text_value
                ),
                "unit": value.entered_unit,
                "show_on_card": field.show_on_card,
                "filterable": field.filterable,
            }
        )
    return rows


def serialize_field(field: SpecField) -> dict:
    data = {
        "key": field.key,
        "label": field.label,
        "help_text": field.help_text,
        "type": field.data_type,
        "quantity": field.quantity,
        "required": field.required,
        "choices": list(field.choices or []),
    }
    if field.quantity:
        quantity = QUANTITIES[field.quantity]
        data["units"] = [{"code": unit.code, "label": unit.label} for unit in quantity.units]
        data["default_unit"] = {
            IMPERIAL: display_unit(field.quantity, IMPERIAL, field.imperial_unit, field.metric_unit),
            METRIC: display_unit(field.quantity, METRIC, field.imperial_unit, field.metric_unit),
        }
    return data


def fields_for_categories(categories: list[Category]) -> dict[str, SpecField]:
    fields: dict[str, SpecField] = {}
    for category in categories:
        for field in category.effective_spec_fields(products_only=True):
            fields.setdefault(field.key, field)
    return fields


def _parse_filter_number(raw: str, parameter: str) -> Decimal:
    if len(raw) > 40:
        raise ValidationError({parameter: "Filter value is too long."})
    try:
        number = Decimal(raw.strip().replace(",", ""))
    except Exception as exc:
        raise ValidationError({parameter: "Use a number."}) from exc
    if not number.is_finite() or not math.isfinite(float(number)):
        raise ValidationError({parameter: "Use a finite number."})
    return number


def apply_spec_filters(
    queryset: QuerySet,
    params,
    fields: dict[str, SpecField],
    system: str,
) -> QuerySet:
    """Apply spec.<key>=value, spec.<key>__min/__max and spec.<key>__unit."""
    requested: dict[str, dict[str, str]] = {}
    for name in params:
        match = SPEC_PARAM_PATTERN.fullmatch(name)
        if match is None:
            if name.startswith("spec."):
                raise ValidationError({name: "Unsupported parameter filter."})
            continue
        requested.setdefault(match.group("key"), {})[match.group("op") or "eq"] = params.get(name, "")
    if len(requested) > MAX_SPEC_FILTERS:
        raise ValidationError({"spec": f"Use at most {MAX_SPEC_FILTERS} parameter filters."})
    for key, operations in requested.items():
        field = fields.get(key)
        if field is None or not field.filterable:
            raise ValidationError({f"spec.{key}": "This parameter cannot be filtered here."})
        values = ProductSpecValue.objects.filter(product=OuterRef("pk"), field__key=key)
        if field.data_type == SpecField.DataType.NUMBER:
            unit = operations.get("unit", "")
            if field.quantity:
                quantity = QUANTITIES[field.quantity]
                unit = unit or display_unit(field.quantity, system, field.imperial_unit, field.metric_unit)
                if quantity.unit(unit) is None:
                    raise ValidationError({f"spec.{key}__unit": "Unsupported unit for this parameter."})
            elif unit:
                raise ValidationError({f"spec.{key}__unit": "This parameter has no unit."})

            def as_base(raw: str, parameter: str) -> Decimal:
                if not field.quantity:
                    return _parse_filter_number(raw, parameter)
                # A typed unit ("95 in", "2413mm") overrides the unit parameter.
                try:
                    number, typed_unit = parse_value_with_unit(raw[:40], field.quantity, unit)
                except ValueError as exc:
                    raise ValidationError({parameter: str(exc)}) from exc
                if not math.isfinite(float(number)):
                    raise ValidationError({parameter: "Use a finite number."})
                return to_base(number, field.quantity, typed_unit)

            if raw := operations.get("eq"):
                target = as_base(raw, f"spec.{key}")
                spread = abs(target) * NUMBER_MATCH_TOLERANCE
                values = values.filter(base_value__gte=target - spread, base_value__lte=target + spread)
            minimum = as_base(operations["min"], f"spec.{key}__min") if operations.get("min") else None
            maximum = as_base(operations["max"], f"spec.{key}__max") if operations.get("max") else None
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValidationError({f"spec.{key}": "Minimum cannot exceed maximum."})
            if minimum is not None:
                values = values.filter(base_value__gte=minimum - abs(minimum) * RANGE_TOLERANCE)
            if maximum is not None:
                values = values.filter(base_value__lte=maximum + abs(maximum) * RANGE_TOLERANCE)
            if not any(operations.get(op) for op in ("eq", "min", "max")):
                continue
        else:
            if set(operations) - {"eq"}:
                raise ValidationError({f"spec.{key}": "Only numeric parameters accept ranges and units."})
            raw = operations.get("eq", "").strip()
            if not raw:
                continue
            if len(raw) > 255 or not raw.isprintable():
                raise ValidationError({f"spec.{key}": "Unsupported filter value."})
            if field.data_type == SpecField.DataType.BOOLEAN:
                if raw.lower() not in {"true", "false"}:
                    raise ValidationError({f"spec.{key}": "Use true or false."})
                values = values.filter(boolean_value=raw.lower() == "true")
            else:
                values = values.filter(text_value__iexact=raw)
        queryset = queryset.filter(Exists(values))
    return queryset


def filter_options(products: QuerySet, fields: dict[str, SpecField], system: str) -> list[dict]:
    options: list[dict] = []
    for field in fields.values():
        if not field.filterable:
            continue
        values = ProductSpecValue.objects.filter(product__in=products, field__key=field.key)
        entry = serialize_field(field)
        if field.data_type == SpecField.DataType.NUMBER:
            bases = sorted({value for value in values.values_list("base_value", flat=True) if value is not None})
            if not bases:
                continue
            if field.quantity:
                entry["values"] = {}
                for target_system in (IMPERIAL, METRIC):
                    unit = entry["default_unit"][target_system]
                    converted = [from_base(base, field.quantity, unit) for base in bases]
                    entry["values"][target_system] = list(dict.fromkeys(
                        format_number(number, converted_places(number)).replace(",", "") for number in converted
                    ))
            else:
                entry["values"] = {target_system: [format_number(base) for base in bases] for target_system in (IMPERIAL, METRIC)}
        elif field.data_type == SpecField.DataType.BOOLEAN:
            if not values.exclude(boolean_value__isnull=True).exists():
                continue
            entry["values"] = ["true", "false"]
        else:
            texts = sorted(
                {text for text in values.values_list("text_value", flat=True) if text},
                key=str.casefold,
            )
            if not texts:
                continue
            entry["values"] = texts
        options.append(entry)
    return options


def parse_spec_input(field: SpecField, raw: object, *, default_system: str = IMPERIAL) -> dict:
    """Validate one buyer-entered RFQ parameter value; returns a stored row."""
    if raw is None or isinstance(raw, (dict, list)):
        raise ValueError("Use text or a number.")
    if isinstance(raw, bool):
        if field.data_type != SpecField.DataType.BOOLEAN:
            raise ValueError("Use text or a number.")
        return {"label": field.label, "value": "Yes" if raw else "No"}
    if isinstance(raw, float) and not math.isfinite(raw):
        raise ValueError("Use a finite number.")
    text = str(raw).strip()
    if len(text) > 160:
        raise ValueError("Ensure this value has at most 160 characters.")
    if not text:
        return {}
    if field.data_type == SpecField.DataType.NUMBER:
        if field.quantity:
            default_unit = display_unit(field.quantity, default_system, field.imperial_unit, field.metric_unit)
            number, unit = parse_value_with_unit(text, field.quantity, default_unit)
            if number <= 0 or number > Decimal("1e12"):
                raise ValueError("Enter a positive value.")
            return {
                "label": field.label,
                "value": format_quantity(number, field.quantity, unit),
                "number": format_number(number, 6).replace(",", ""),
                "unit": unit,
            }
        number = parse_number(text)
        if not number.is_finite() or number < 0 or number > Decimal("1e12"):
            raise ValueError("Enter a positive number.")
        return {"label": field.label, "value": format_number(number, 6), "number": format_number(number, 6).replace(",", "")}
    if field.data_type == SpecField.DataType.CHOICE:
        choice = next((item for item in field.choices if item.casefold() == text.casefold()), None)
        if choice is None and text.casefold() not in {"other", "not sure", "discuss"}:
            raise ValueError("Select one of the listed options.")
        return {"label": field.label, "value": choice or text}
    if field.data_type == SpecField.DataType.BOOLEAN:
        mapped = {"yes": "Yes", "true": "Yes", "no": "No", "false": "No", "discuss": "Discuss"}.get(text.casefold())
        if mapped is None:
            raise ValueError("Select yes, no or discuss.")
        return {"label": field.label, "value": mapped}
    return {"label": field.label, "value": text}


def build_search_document(product: Product) -> str:
    parts: list[str] = [
        product.title,
        product.model,
        product.sku,
        product.short_description,
        product.description,
        product.packaging,
        *(category.name for category in product.category.lineage()),
        *(application.name for application in product.applications.filter(is_active=True)),
        *(document.title for document in product.documents.filter(is_active=True)),
        *(document.text_note for document in product.documents.filter(is_active=True)),
    ]
    for row in serialize_spec_values(product):
        parts.append(row["label"])
        parts.extend({row["display"][IMPERIAL], row["display"][METRIC]})
    for language_fields in (product.translations or {}).values():
        parts.extend(text for text in language_fields.values() if isinstance(text, str))
    return "\n".join(part.strip() for part in parts if part and part.strip())[:60_000]


def refresh_search_document(product_id: int) -> None:
    product = (
        Product.objects.select_related("category", "category__parent")
        .filter(pk=product_id)
        .first()
    )
    if product is None:
        return
    document = build_search_document(product)
    if document != product.search_document:
        Product.objects.filter(pk=product_id).update(search_document=document)

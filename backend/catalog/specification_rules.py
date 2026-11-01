from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


BRAKE_ALIASES = {
    "electric": "Electric Drum",
    "electric brake": "Electric Drum",
    "electric drum": "Electric Drum",
    "electric drum brake": "Electric Drum",
    "hydraulic": "Hydraulic Drum",
    "hydraulic brake": "Hydraulic Drum",
    "hydraulic drum": "Hydraulic Drum",
    "hydraulic drum brake": "Hydraulic Drum",
    "disc": "Hydraulic Disc",
    "disc brake": "Hydraulic Disc",
    "hydraulic disc": "Hydraulic Disc",
    "hydraulic disc brake": "Hydraulic Disc",
    "no brake": "No Brake",
    "none": "No Brake",
    "idler": "No Brake",
    "other": "Other",
    "custom": "Other",
}

SURFACE_FINISH_ALIASES = {
    "paint": "Painted",
    "painted": "Painted",
    "e-coat": "E-Coated",
    "e coat": "E-Coated",
    "e-coated": "E-Coated",
    "e coated": "E-Coated",
    "powder coat": "Powder-Coated",
    "powder-coated": "Powder-Coated",
    "powder coated": "Powder-Coated",
    "zinc": "Zinc Plated",
    "zinc plated": "Zinc Plated",
    "hot-dip galvanised": "Hot-Dip Galvanized",
    "hot dip galvanised": "Hot-Dip Galvanized",
    "hot-dip galvanized": "Hot-Dip Galvanized",
    "hot dip galvanized": "Hot-Dip Galvanized",
    "galvanised": "Hot-Dip Galvanized",
    "galvanized": "Hot-Dip Galvanized",
    "hdg": "Hot-Dip Galvanized",
    "custom": "Custom Finish",
    "custom finish": "Custom Finish",
    "other": "Custom Finish",
}

TUBE_SHAPE_ALIASES = {
    "round": "Round",
    "round tube": "Round",
    "square": "Square",
    "square tube": "Square",
    "other": "Other",
    "custom": "Other",
}

DIMENSION_PATTERN = re.compile(
    r'^\s*(?P<number>(?:\d+(?:\.\d*)?|\.\d+))\s*'
    r'(?P<unit>"|in(?:ch(?:es)?)?|mm)?\s*$',
    re.IGNORECASE,
)

# Match the public specification field limit, including the expanded decimal
# representation. A short scientific-notation input must not produce an
# arbitrarily large string when formatted with "f".
MAX_NUMBER_CHARACTERS = 160


def _validate_decimal_size(value: Decimal) -> None:
    if not value.is_finite():
        raise ValueError("Use a finite number.")
    sign, digits, exponent = value.as_tuple()
    if len(digits) > MAX_NUMBER_CHARACTERS or abs(exponent) > MAX_NUMBER_CHARACTERS:
        raise ValueError("Number has too many digits or an out-of-range exponent.")

    if exponent >= 0:
        rendered_length = len(digits) + exponent
    elif len(digits) + exponent > 0:
        rendered_length = len(digits) + 1  # decimal point within the coefficient
    else:
        rendered_length = 2 - exponent  # leading "0." and fractional places
    if sign + rendered_length > MAX_NUMBER_CHARACTERS:
        raise ValueError(
            f"Expanded number must have at most {MAX_NUMBER_CHARACTERS} characters."
        )


def normalize_choice_value(
    value: str,
    *,
    aliases: dict[str, str],
    allowed: list[str],
) -> str | None:
    key = value.strip().casefold()
    normalized = aliases.get(key)
    if normalized is not None:
        return normalized
    return next(
        (choice for choice in allowed if choice.casefold() == key),
        None,
    )


def parse_dimension_value(value: str) -> tuple[Decimal, str | None]:
    """Parse a positive dimension with an optional inch or millimetre suffix."""
    if len(value) > MAX_NUMBER_CHARACTERS:
        raise ValueError(f"Ensure this value has at most {MAX_NUMBER_CHARACTERS} characters.")
    match = DIMENSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Use a positive number with an optional in, inch or mm unit.")
    try:
        number = Decimal(match.group("number"))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid dimension.") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("Dimension must be a finite positive number.")
    _validate_decimal_size(number)
    suffix = (match.group("unit") or "").casefold()
    if suffix == "mm":
        unit = "mm"
    elif suffix:
        unit = "in"
    else:
        unit = None
    return number, unit


def compact_decimal(value: Decimal) -> str:
    _validate_decimal_size(value)
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def parse_positive_number(
    value: str,
    *,
    maximum: Decimal,
    allow_commas: bool = False,
) -> Decimal:
    if len(value) > MAX_NUMBER_CHARACTERS:
        raise ValueError(f"Ensure this value has at most {MAX_NUMBER_CHARACTERS} characters.")
    candidate = value.strip()
    if allow_commas:
        if "," in candidate and not re.fullmatch(
            r"\d{1,3}(?:,\d{3})*(?:\.\d+)?",
            candidate,
        ):
            raise ValueError("Use standard thousands separators, for example 10,000.")
        candidate = candidate.replace(",", "")
    try:
        number = Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid number.") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("Value must be a finite positive number.")
    _validate_decimal_size(number)
    if number > maximum:
        raise ValueError(f"Value must not exceed {compact_decimal(maximum)}.")
    return number

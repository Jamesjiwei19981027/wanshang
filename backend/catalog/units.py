"""Unit registry shared by product specifications, filters, search and the assistant.

Every quantity stores values in one base unit. Linear units convert with a
factor (value_in_base = value * factor); temperature also needs an offset.
Each quantity names its default display unit for the imperial and metric
systems. The site default is imperial (lb, in); visitors can switch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

IMPERIAL = "imperial"
METRIC = "metric"
UNIT_SYSTEMS = (IMPERIAL, METRIC)


@dataclass(frozen=True)
class Unit:
    code: str
    label: str
    factor: Decimal
    offset: Decimal = Decimal("0")
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Quantity:
    code: str
    label: str
    base: str
    imperial: str
    metric: str
    units: tuple[Unit, ...]

    def unit(self, code: str) -> Unit | None:
        return next((unit for unit in self.units if unit.code == code), None)


def _d(value: str) -> Decimal:
    return Decimal(value)


QUANTITIES: dict[str, Quantity] = {
    quantity.code: quantity
    for quantity in (
        Quantity("mass", "Weight / load", "kg", "lb", "kg", (
            Unit("kg", "kg", _d("1"), aliases=("kgs", "kilogram", "kilograms")),
            Unit("lb", "lb", _d("0.45359237"), aliases=("lbs", "pound", "pounds")),
            Unit("t", "t", _d("1000"), aliases=("ton", "tons", "tonne", "tonnes")),
            Unit("g", "g", _d("0.001"), aliases=("gram", "grams")),
        )),
        Quantity("length", "Length / dimension", "mm", "in", "mm", (
            Unit("mm", "mm", _d("1"), aliases=("millimeter", "millimeters", "millimetre", "millimetres")),
            Unit("cm", "cm", _d("10"), aliases=("centimeter", "centimeters", "centimetre", "centimetres")),
            Unit("m", "m", _d("1000"), aliases=("meter", "meters", "metre", "metres")),
            Unit("in", "in", _d("25.4"), aliases=('"', "inch", "inches")),
            Unit("ft", "ft", _d("304.8"), aliases=("'", "foot", "feet")),
        )),
        Quantity("volume", "Volume", "l", "gal", "l", (
            Unit("l", "L", _d("1"), aliases=("liter", "liters", "litre", "litres")),
            Unit("ml", "mL", _d("0.001"), aliases=("milliliter", "milliliters")),
            Unit("m3", "m³", _d("1000"), aliases=("m³", "cbm", "cubic meter", "cubic metre")),
            Unit("gal", "gal", _d("3.785411784"), aliases=("gallon", "gallons")),
        )),
        Quantity("power", "Power", "kw", "hp", "kw", (
            Unit("kw", "kW", _d("1"), aliases=("kilowatt", "kilowatts")),
            Unit("w", "W", _d("0.001"), aliases=("watt", "watts")),
            Unit("hp", "hp", _d("0.745699872"), aliases=("horsepower",)),
        )),
        Quantity("pressure", "Pressure", "kpa", "psi", "bar", (
            Unit("kpa", "kPa", _d("1")),
            Unit("mpa", "MPa", _d("1000")),
            Unit("bar", "bar", _d("100")),
            Unit("psi", "psi", _d("6.894757293168")),
        )),
        Quantity("temperature", "Temperature", "c", "f", "c", (
            Unit("c", "°C", _d("1"), aliases=("°c", "celsius")),
            Unit("f", "°F", _d("0.5555555555555555555555555556"), _d("-17.77777777777777777777777778"), aliases=("°f", "fahrenheit")),
        )),
        Quantity("voltage", "Voltage", "v", "v", "v", (
            Unit("v", "V", _d("1"), aliases=("volt", "volts")),
            Unit("kv", "kV", _d("1000")),
        )),
        Quantity("current", "Electric current", "a", "a", "a", (
            Unit("a", "A", _d("1"), aliases=("amp", "amps", "ampere")),
            Unit("ma", "mA", _d("0.001")),
        )),
        Quantity("torque", "Torque", "nm", "lbft", "nm", (
            Unit("nm", "N·m", _d("1"), aliases=("n·m", "n.m", "n-m")),
            Unit("lbft", "lb·ft", _d("1.3558179483314"), aliases=("lb·ft", "lb-ft", "ft-lb", "ft·lb", "lbf·ft")),
        )),
        Quantity("force", "Force", "n", "lbf", "kn", (
            Unit("n", "N", _d("1"), aliases=("newton", "newtons")),
            Unit("kn", "kN", _d("1000")),
            Unit("lbf", "lbf", _d("4.4482216152605")),
        )),
        Quantity("speed", "Speed", "kmh", "mph", "kmh", (
            Unit("kmh", "km/h", _d("1"), aliases=("km/h", "kph")),
            Unit("mph", "mph", _d("1.609344")),
        )),
    )
}

QUANTITY_CHOICES = [(quantity.code, quantity.label) for quantity in QUANTITIES.values()]


def unit_choices(quantity_code: str) -> list[tuple[str, str]]:
    quantity = QUANTITIES.get(quantity_code)
    return [(unit.code, unit.label) for unit in quantity.units] if quantity else []


def all_unit_codes() -> set[str]:
    return {unit.code for quantity in QUANTITIES.values() for unit in quantity.units}


def to_base(value: Decimal, quantity_code: str, unit_code: str) -> Decimal:
    unit = QUANTITIES[quantity_code].unit(unit_code)
    if unit is None:
        raise ValueError(f"Unit {unit_code} does not measure {quantity_code}.")
    return value * unit.factor + unit.offset


def from_base(value: Decimal, quantity_code: str, unit_code: str) -> Decimal:
    unit = QUANTITIES[quantity_code].unit(unit_code)
    if unit is None:
        raise ValueError(f"Unit {unit_code} does not measure {quantity_code}.")
    return (value - unit.offset) / unit.factor


def convert(value: Decimal, quantity_code: str, from_unit: str, to_unit: str) -> Decimal:
    if from_unit == to_unit:
        return value
    return from_base(to_base(value, quantity_code, from_unit), quantity_code, to_unit)


def display_unit(quantity_code: str, system: str, imperial_override: str = "", metric_override: str = "") -> str:
    quantity = QUANTITIES[quantity_code]
    if system == METRIC:
        return metric_override or quantity.metric
    return imperial_override or quantity.imperial


def format_number(value: Decimal, places: int = 2) -> str:
    """Round for display and group thousands: 3500 -> 3,500; 95.250 -> 95.25."""
    quantized = value.quantize(Decimal(1).scaleb(-places))
    rendered = f"{quantized:,.{places}f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def converted_places(value: Decimal) -> int:
    """Decimal places for a converted value: about four significant digits."""
    magnitude = abs(value)
    if magnitude >= 1000:
        return 0
    if magnitude >= 100:
        return 1
    if magnitude >= 10:
        return 2
    return 3


def format_quantity(value: Decimal, quantity_code: str, unit_code: str, *, places: int = 6) -> str:
    unit = QUANTITIES[quantity_code].unit(unit_code)
    label = unit.label if unit else unit_code
    separator = "" if label.startswith("°") else " "
    return f"{format_number(value, places)}{separator}{label}"


_ALIAS_INDEX: dict[str, list[tuple[str, str]]] = {}
for _quantity in QUANTITIES.values():
    for _unit in _quantity.units:
        for _name in {_unit.code, _unit.label.casefold(), *(alias.casefold() for alias in _unit.aliases)}:
            pairs = _ALIAS_INDEX.setdefault(_name.casefold(), [])
            if (_quantity.code, _unit.code) not in pairs:
                pairs.append((_quantity.code, _unit.code))


def resolve_unit(token: str) -> list[tuple[str, str]]:
    """Return (quantity, unit) pairs for a typed unit such as 'lbs' or 'mm'."""
    return list(_ALIAS_INDEX.get(token.strip().casefold(), []))


_UNIT_ALTERNATION = "|".join(
    sorted((re.escape(name) for name in _ALIAS_INDEX), key=len, reverse=True)
)
QUANTITY_PATTERN = re.compile(
    rf"(?<![\w.])(?P<number>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_ALTERNATION})(?![\w])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuantityMention:
    text: str
    value: Decimal
    quantity: str
    unit: str
    base_value: Decimal


def find_quantities(text: str) -> list[QuantityMention]:
    """Extract values such as '3,500 lb' or '95mm' from free text."""
    mentions: list[QuantityMention] = []
    for match in QUANTITY_PATTERN.finditer(text):
        try:
            number = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        for quantity_code, unit_code in resolve_unit(match.group("unit")):
            mentions.append(
                QuantityMention(
                    text=match.group(0),
                    value=number,
                    quantity=quantity_code,
                    unit=unit_code,
                    base_value=to_base(number, quantity_code, unit_code),
                )
            )
    return mentions


def parse_number(raw: str) -> Decimal:
    candidate = raw.strip().replace(",", "")
    if not candidate or len(candidate) > 40:
        raise ValueError("Enter a number.")
    try:
        value = Decimal(candidate)
    except InvalidOperation as exc:
        raise ValueError("Enter a number.") from exc
    if not value.is_finite():
        raise ValueError("Enter a finite number.")
    return value


def parse_value_with_unit(raw: str, quantity_code: str, default_unit: str) -> tuple[Decimal, str]:
    """Parse '95', '95 mm' or '3.74 in' for one quantity."""
    text = raw.strip()
    match = re.fullmatch(r"(?P<number>[-+]?[\d,]*\.?\d+)\s*(?P<unit>.*)", text)
    if match is None:
        raise ValueError("Enter a number with an optional unit.")
    number = parse_number(match.group("number"))
    unit_text = match.group("unit").strip()
    if not unit_text:
        return number, default_unit
    for candidate_quantity, unit_code in resolve_unit(unit_text):
        if candidate_quantity == quantity_code:
            return number, unit_code
    raise ValueError(f"Use a {QUANTITIES[quantity_code].label.lower()} unit.")


def public_registry() -> list[dict]:
    return [
        {
            "code": quantity.code,
            "label": quantity.label,
            "imperial": quantity.imperial,
            "metric": quantity.metric,
            "units": [{"code": unit.code, "label": unit.label} for unit in quantity.units],
        }
        for quantity in QUANTITIES.values()
    ]

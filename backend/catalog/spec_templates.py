"""Starter parameter lists. Staff edit them under Categories afterwards.

The trailer-axle list carries over every field of the original axle-only
product form. Other categories start empty until the company supplies
their parameters.
"""

from __future__ import annotations

from .models import Category, SpecField

N, T, C, B = SpecField.DataType.NUMBER, SpecField.DataType.TEXT, SpecField.DataType.CHOICE, SpecField.DataType.BOOLEAN

# key, label, type, quantity, choices, filterable, show_on_card, ask_in_rfq, used_by_products
TRAILER_AXLE_PARAMETERS = (
    ("rated_capacity", "Rated capacity", N, "mass", (), True, True, True, True),
    ("axle_type", "Axle type", C, "", ("Straight", "Drop", "Torsion", "Other"), True, True, True, True),
    ("brake_type", "Brake", C, "", ("No brake", "Electric drum", "Hydraulic drum", "Hydraulic disc", "Air drum", "Other"), True, True, True, True),
    ("bolt_pattern", "Bolt pattern", T, "", (), True, True, True, True),
    ("hub_face", "Hub face", N, "length", (), True, True, True, True),
    ("spring_center", "Spring centre", N, "length", (), True, True, True, True),
    ("overall_length", "Overall length", N, "length", (), False, False, True, True),
    ("tube_shape", "Tube shape", C, "", ("Round", "Square", "Other"), True, False, True, True),
    ("tube_size", "Tube size", T, "", (), False, False, True, True),
    ("tube_wall_thickness", "Tube wall thickness", N, "length", (), False, False, False, True),
    ("drop_height", "Drop height", N, "length", (), False, False, True, True),
    ("stud_size", "Stud size", T, "", (), False, False, True, True),
    ("hub_type", "Hub type", T, "", (), False, False, True, True),
    ("spindle_type", "Spindle type", T, "", (), False, False, False, True),
    ("inner_bearing", "Inner bearing", T, "", (), False, False, False, True),
    ("outer_bearing", "Outer bearing", T, "", (), False, False, False, True),
    ("seal_size", "Seal size", T, "", (), False, False, False, True),
    ("brake_size", "Brake size", T, "", (), False, False, True, True),
    ("brake_voltage", "Brake voltage", N, "voltage", (), False, False, False, True),
    ("grease_cap_type", "Grease cap", T, "", (), False, False, False, True),
    ("axle_material", "Axle material", T, "", (), False, False, False, True),
    ("surface_finish", "Surface finish", C, "", ("Painted", "E-coated", "Powder-coated", "Zinc plated", "Hot-dip galvanized", "Custom finish"), True, False, True, True),
    ("corrosion_protection", "Corrosion protection", T, "", (), False, False, False, True),
    # Questions for the buyer only; products do not carry these values.
    ("trailer_weight", "Estimated loaded trailer weight", N, "mass", (), False, False, True, False),
    ("axle_count", "Axles per trailer", C, "", ("Single", "Tandem", "Triple", "Not sure"), False, False, True, False),
    ("operating_environment", "Operating environment", T, "", (), False, False, True, False),
)


def apply_template(category: Category, parameters, *, overwrite: bool = False) -> int:
    """Create missing parameters; keep staff edits unless overwrite is set."""
    created = 0
    for position, (key, label, data_type, quantity, choices, filterable, card, rfq, products) in enumerate(parameters):
        defaults = {
            "label": label,
            "data_type": data_type,
            "quantity": quantity,
            "choices": list(choices),
            "filterable": filterable,
            "show_on_card": card,
            "ask_in_rfq": rfq,
            "used_by_products": products,
            "position": position * 10,
        }
        field = SpecField.objects.filter(category=category, key=key).first()
        if field is None:
            field = SpecField(category=category, key=key, **defaults)
            created += 1
        elif overwrite:
            for name, value in defaults.items():
                setattr(field, name, value)
        else:
            continue
        field.full_clean()
        field.save()
    return created


CATEGORY_TEMPLATES = {"trailer-axles": TRAILER_AXLE_PARAMETERS}

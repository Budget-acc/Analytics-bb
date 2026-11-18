"""
Measurement page layout shared by every template.

Key-value cards ("Measurement | Size") and per-person tables (measurements as
rows, up to 4 person columns per table) use the same fixed column widths, so
a key-value card and a 1-person table are exactly the same size and each
template can style both with one table class. The per-person chunking reuses
the legacy helper, so the data matches the existing invoice.
"""
from whatsapp_integration.v3.pdf_template import BasePDFService

S = BasePDFService

# Measurement tables are drawn at 80% of their original size (column widths,
# fonts and padding); each template scales its own sizes with scaled().
MEASUREMENT_SCALE = 0.8

LABEL_COL_PX = round(225 * MEASUREMENT_SCALE)   # 180 - 'Measurement(s)' column
VALUE_COL_PX = round(120 * MEASUREMENT_SCALE)   # 96 - 'Size' column / each person column


def scaled(value) -> str:
    """A CSS number at MEASUREMENT_SCALE, e.g. scaled(11) -> '8.8', scaled(10) -> '8'."""
    return f"{round(value * MEASUREMENT_SCALE, 2):g}"


def measurement_layout(data) -> dict:
    """Template data for a MeasurementData: pairs of key-value cards + per-person tables."""
    pairs = [data.sections[i:i + 2] for i in range(0, len(data.sections), 2)]
    people = [
        {
            "heading": heading,
            "tables": [
                {"names": names, "rows": body, "width_px": LABEL_COL_PX + VALUE_COL_PX * len(names)}
                for names, body in S._person_measurement_tables(columns, rows)
            ],
        }
        for heading, columns, rows in data.person_sections
    ]
    return {
        "data": data,
        "pairs": pairs,
        "people": people,
        "label_col_px": LABEL_COL_PX,
        "value_col_px": VALUE_COL_PX,
        "card_width_px": LABEL_COL_PX + VALUE_COL_PX,
    }

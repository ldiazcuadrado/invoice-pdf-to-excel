"""CSV export of the invoice table."""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from invoice_to_excel.models import Result
from invoice_to_excel.reporting.tables import (
    INVOICE_COLUMNS,
    extraction_label,
    notes_text,
    rate_label,
    sorted_invoices,
    status_label,
)


def render_csv(result: Result, locale: str = "en") -> str:
    """Render the invoice table.

    locale "en": comma separator, dot decimal, ISO dates.
    locale "es": semicolon separator, decimal comma, dd/mm/yyyy dates (opens directly
    in Spanish-locale Excel).
    """
    if locale not in ("en", "es"):
        raise ValueError(f"Unknown CSV locale: {locale!r}")
    spanish = locale == "es"

    def num(value: Decimal | None) -> str:
        text = "" if value is None else f"{value:.2f}"
        return text.replace(".", ",") if spanish else text

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";" if spanish else ",", lineterminator="\r\n")
    writer.writerow([c.title for c in INVOICE_COLUMNS])
    for invoice in sorted_invoices(result.invoices):
        d = invoice.invoice_date
        date_text = "" if d is None else d.strftime("%d/%m/%Y" if spanish else "%Y-%m-%d")
        writer.writerow(
            [
                invoice.supplier or "",
                invoice.tax_id or "",
                invoice.number or "",
                date_text,
                num(invoice.net_amount),
                rate_label(invoice, spanish),
                num(invoice.vat_amount),
                num(invoice.surcharge_amount),
                num(invoice.irpf),
                num(invoice.total),
                f"{invoice.overall_confidence:.2f}".replace(".", ",")
                if spanish
                else f"{invoice.overall_confidence:.2f}",
                extraction_label(invoice),
                invoice.filename,
                status_label(invoice),
                notes_text(invoice),
            ]
        )
    return buffer.getvalue()


def write_csv(result: Result, target: Path, locale: str = "en") -> Path:
    """Write the CSV as UTF-8 with BOM so Excel detects the encoding."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        fh.write(render_csv(result, locale))
    return target

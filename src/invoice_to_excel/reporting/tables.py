"""Column definitions and cell formatting shared by the Excel and CSV reports."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from invoice_to_excel.models import Invoice


@dataclass(frozen=True)
class Column:
    title: str
    width: int
    field: str | None = None  # key in Invoice.confidence that drives low-confidence highlighting


INVOICE_COLUMNS = (
    Column("Supplier", 34, "supplier"),
    Column("Tax ID", 13, "tax_id"),
    Column("Invoice no.", 16, "number"),
    Column("Date", 12, "invoice_date"),
    Column("Net amount", 16, "net_amount"),
    Column("VAT %", 14, "vat_amount"),
    Column("VAT amount", 14, "vat_amount"),
    Column("Equivalence surcharge", 14),
    Column("Withholding (IRPF)", 18, "irpf"),
    Column("Total", 14, "total"),
    Column("Confidence", 12),
    Column("Extraction", 13),
    Column("Source file", 28),
    Column("Status", 10),
    Column("Notes", 70),
)


def format_rate(rate: Decimal | None) -> str:
    return "?" if rate is None else f"{rate:f}%"


def rate_label(invoice: Invoice, decimal_comma: bool = False) -> str:
    """'21%' for one rate, '21% + 10%' for several, blank when none was found."""
    rates: list[str] = []
    for line in invoice.tax_lines:
        label = format_rate(line.rate)
        if label not in rates:
            rates.append(label)
    text = " + ".join(rates)
    return text.replace(".", ",") if decimal_comma else text


def extraction_label(invoice: Invoice) -> str:
    return f"{invoice.source} + OCR" if invoice.ocr else invoice.source


def status_label(invoice: Invoice) -> str:
    return "OK" if invoice.is_ok else "Review"


def notes_text(invoice: Invoice) -> str:
    return "; ".join(invoice.review_reasons)


def sorted_invoices(invoices: list[Invoice]) -> list[Invoice]:
    return sorted(invoices, key=lambda i: (i.invoice_date is None, i.invoice_date, i.filename))

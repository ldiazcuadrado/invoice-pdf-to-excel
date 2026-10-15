"""Consistency checks on extracted invoices."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from invoice_to_excel.models import Invoice, TaxLine
from invoice_to_excel.validation.tax_id import is_valid_tax_id, normalize_tax_id

TOLERANCE = Decimal("0.02")
CENT = Decimal("0.01")


def _percent_of(base: Decimal, rate: Decimal) -> Decimal:
    return (base * rate / 100).quantize(CENT)


def _label(line: TaxLine) -> str:
    return f"{line.rate}%" if line.rate is not None else "unknown rate"


def _check_line(line: TaxLine) -> list[str]:
    problems: list[str] = []
    if line.base is not None and line.rate is not None and line.vat is not None:
        expected = _percent_of(line.base, line.rate)
        if abs(expected - line.vat) > TOLERANCE:
            problems.append(
                f"VAT amount ({line.vat}) does not match {line.rate}% "
                f"of the net amount {line.base} (expected {expected})"
            )
    if line.base is not None and line.surcharge_rate is not None and line.surcharge is not None:
        expected = _percent_of(line.base, line.surcharge_rate)
        if abs(expected - line.surcharge) > TOLERANCE:
            problems.append(
                f"Equivalence surcharge for {_label(line)} ({line.surcharge}) does not match "
                f"{line.surcharge_rate}% of {line.base} (expected {expected})"
            )
    return problems


def validate_invoice(invoice: Invoice) -> list[str]:
    """Replace `invoice.problems` with the validation findings and return them."""
    p: list[str] = []
    if not invoice.supplier:
        p.append("Supplier not found")
    if not invoice.tax_id:
        p.append("Issuer tax ID (NIF/CIF) not found")
    elif not is_valid_tax_id(invoice.tax_id):
        p.append(f"Issuer tax ID (NIF/CIF) is not valid ({invoice.tax_id})")
    if not invoice.number:
        p.append("Invoice number not found")
    if invoice.invoice_date is None:
        p.append("Date not found")
    if invoice.net_amount is None:
        p.append("Net amount (base imponible) not found")
    if invoice.total is None:
        p.append("Total not found")

    for line in invoice.tax_lines:
        p.extend(_check_line(line))

    net = invoice.net_amount
    if net is not None and invoice.total is not None:
        expected_total = (
            net
            + (invoice.vat_amount or Decimal(0))
            + (invoice.surcharge_amount or Decimal(0))
            - (invoice.irpf or Decimal(0))
        )
        diff = invoice.total - expected_total
        tolerance = TOLERANCE * max(1, len(invoice.tax_lines))
        if abs(diff) > tolerance:
            surcharge = " + surcharge" if invoice.surcharge_amount else ""
            p.append(
                f"Total does not add up: net + VAT{surcharge} - IRPF = {expected_total:.2f}, "
                f"the invoice states {invoice.total:.2f} (difference {diff:+.2f})"
            )
    invoice.problems = p
    return p


def flag_duplicates(invoices: Iterable[Invoice]) -> None:
    """Mark invoices repeating the issuer and number of an earlier one. Idempotent."""
    seen: dict[tuple[str, str], Invoice] = {}
    for invoice in invoices:
        invoice.duplicate_of = None
        if not invoice.tax_id or not invoice.number:
            continue
        key = (normalize_tax_id(invoice.tax_id), invoice.number.upper())
        if key in seen:
            invoice.duplicate_of = seen[key].filename
        else:
            seen[key] = invoice

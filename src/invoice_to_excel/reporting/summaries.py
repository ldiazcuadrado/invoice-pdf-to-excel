"""Accountant-oriented aggregations: quarterly input VAT (modelo 303) and annual
supplier totals (modelo 347).

Invoices flagged as duplicates are excluded so the same operation is never counted
twice. Invoices that need review are included and counted separately, so the
accountant can see how much of a figure rests on unverified data.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from invoice_to_excel.models import Invoice
from invoice_to_excel.validation.tax_id import normalize_tax_id

SUPPLIER_347_THRESHOLD = Decimal("3005.06")
NO_DATE = "No date"

_ZERO = Decimal(0)


class VatQuarterRow(BaseModel):
    """Input VAT for one quarter and one rate."""

    model_config = ConfigDict(frozen=True)

    period: str
    rate: Decimal | None
    invoices: int
    base: Decimal
    vat: Decimal
    surcharge: Decimal
    to_review: int


class SupplierYearRow(BaseModel):
    """Annual operations with one supplier, VAT included, for the modelo 347 check."""

    model_config = ConfigDict(frozen=True)

    year: int | None
    supplier: str
    tax_id: str | None
    invoices: int
    gross: Decimal
    over_threshold: bool


def quarter_of(invoice: Invoice) -> str:
    d = invoice.invoice_date
    return NO_DATE if d is None else f"{d.year}-T{(d.month - 1) // 3 + 1}"


def _countable(invoices: Iterable[Invoice]) -> list[Invoice]:
    return [i for i in invoices if i.duplicate_of is None]


@dataclass
class _VatBucket:
    invoices: int = 0
    to_review: int = 0
    base: Decimal = _ZERO
    vat: Decimal = _ZERO
    surcharge: Decimal = _ZERO


def quarterly_vat_summary(invoices: Iterable[Invoice]) -> list[VatQuarterRow]:
    """Base, VAT and surcharge per (quarter, rate), ordered chronologically then by rate."""
    buckets: dict[tuple[str, Decimal | None], _VatBucket] = defaultdict(_VatBucket)
    for invoice in _countable(invoices):
        period = quarter_of(invoice)
        for rate in {line.rate for line in invoice.tax_lines}:
            bucket = buckets[(period, rate)]
            bucket.invoices += 1
            bucket.to_review += 0 if invoice.is_ok else 1
            for line in (ln for ln in invoice.tax_lines if ln.rate == rate):
                bucket.base += line.base or _ZERO
                bucket.vat += line.vat or _ZERO
                bucket.surcharge += line.surcharge or _ZERO

    def order(key: tuple[str, Decimal | None]) -> tuple[bool, str, bool, Decimal]:
        period, rate = key
        return (period == NO_DATE, period, rate is None, -(rate or _ZERO))

    return [
        VatQuarterRow(
            period=period,
            rate=rate,
            invoices=b.invoices,
            base=b.base,
            vat=b.vat,
            surcharge=b.surcharge,
            to_review=b.to_review,
        )
        for (period, rate), b in sorted(buckets.items(), key=lambda kv: order(kv[0]))
    ]


@dataclass
class _SupplierTotal:
    name: str
    tax_id: str | None
    invoices: int = 0
    gross: Decimal = _ZERO


def supplier_annual_totals(
    invoices: Iterable[Invoice], threshold: Decimal = SUPPLIER_347_THRESHOLD
) -> list[SupplierYearRow]:
    """Gross operations per supplier and calendar year, flagging those above `threshold`.

    The amount is base + VAT + surcharge: IRPF withholdings do not reduce the
    operation value reported in the modelo 347.
    """
    totals: dict[tuple[int | None, str], _SupplierTotal] = {}
    for invoice in _countable(invoices):
        year = invoice.invoice_date.year if invoice.invoice_date else None
        tax_id = normalize_tax_id(invoice.tax_id) if invoice.tax_id else None
        name = invoice.supplier or "(no supplier)"
        entry = totals.setdefault((year, tax_id or name.casefold()), _SupplierTotal(name, tax_id))
        entry.invoices += 1
        entry.gross += invoice.gross_amount or _ZERO

    rows = [
        SupplierYearRow(
            year=year,
            supplier=t.name,
            tax_id=t.tax_id,
            invoices=t.invoices,
            gross=t.gross,
            over_threshold=t.gross > threshold,
        )
        for (year, _), t in totals.items()
    ]
    rows.sort(key=lambda r: (r.year is None, r.year or 0, -r.gross, r.supplier))
    return rows

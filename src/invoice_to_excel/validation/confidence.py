"""Confidence scoring.

Extractors report how each field was found. This module adjusts those scores with
independent evidence: a valid tax ID check character or amounts that reconcile are
strong signals, a failed check is a strong negative one.
"""

from __future__ import annotations

from invoice_to_excel.models import Invoice
from invoice_to_excel.validation.tax_id import is_valid_tax_id

AMOUNT_FIELDS = ("net_amount", "vat_amount", "total")
CHECKED_FLOOR = 0.95
RECONCILED_FLOOR = 0.9
FAILED_CEILING = 0.4


def scale_confidence(invoice: Invoice, factor: float) -> None:
    """Scale every field score, e.g. to reflect OCR noise."""
    invoice.confidence = {
        name: round(value * factor, 3) for name, value in invoice.confidence.items()
    }


def ocr_factor(mean_word_confidence: float | None) -> float:
    """Map Tesseract's mean word confidence (0-100) to a multiplier for field scores."""
    if mean_word_confidence is None:
        return 0.85
    return round(0.9 * min(max(mean_word_confidence, 0.0), 100.0) / 100.0, 3)


def adjust_confidence(invoice: Invoice) -> None:
    """Fold validation outcomes into the field scores and zero out missing fields.

    Call after `validate_invoice`.
    """
    scores = dict(invoice.confidence)

    if invoice.tax_id:
        if is_valid_tax_id(invoice.tax_id):
            scores["tax_id"] = max(scores.get("tax_id", 0.0), CHECKED_FLOOR)
        else:
            scores["tax_id"] = min(scores.get("tax_id", 0.0), FAILED_CEILING)

    amount_problem = any("add up" in p or "VAT amount" in p for p in invoice.problems)
    if invoice.net_amount is not None and invoice.total is not None:
        for name in AMOUNT_FIELDS:
            if amount_problem:
                scores[name] = min(scores.get(name, 0.0), FAILED_CEILING)
            else:
                scores[name] = max(scores.get(name, 0.0), RECONCILED_FLOOR)

    present = {
        "supplier": invoice.supplier,
        "tax_id": invoice.tax_id,
        "number": invoice.number,
        "invoice_date": invoice.invoice_date,
        "net_amount": invoice.net_amount,
        "vat_amount": invoice.vat_amount,
        "total": invoice.total,
        "irpf": invoice.irpf,
    }
    for name, value in present.items():
        if value is None:
            scores[name] = 0.0
    if invoice.irpf is None:
        scores.pop("irpf", None)  # no withholding is not a missing field
    invoice.confidence = {name: round(min(max(v, 0.0), 1.0), 3) for name, v in scores.items()}

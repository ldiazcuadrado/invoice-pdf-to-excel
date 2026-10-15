"""Validation of tax identifiers and invoice arithmetic."""

from invoice_to_excel.validation.confidence import adjust_confidence, ocr_factor, scale_confidence
from invoice_to_excel.validation.invoice import TOLERANCE, flag_duplicates, validate_invoice
from invoice_to_excel.validation.tax_id import (
    cif_check,
    dni_letter,
    is_valid_tax_id,
    normalize_tax_id,
    tax_id_kind,
)

__all__ = [
    "TOLERANCE",
    "adjust_confidence",
    "cif_check",
    "dni_letter",
    "flag_duplicates",
    "is_valid_tax_id",
    "normalize_tax_id",
    "ocr_factor",
    "scale_confidence",
    "tax_id_kind",
    "validate_invoice",
]

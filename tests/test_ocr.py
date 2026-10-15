from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytesseract
import pytest

from invoice_to_excel.config import Settings
from invoice_to_excel.extraction import Pipeline, build_pipeline
from invoice_to_excel.extraction.ocr import (
    OcrEngine,
    OcrUnavailableError,
    _lines_from_data,
    resolve_languages,
)
from invoice_to_excel.extraction.pipeline import OcrReader
from invoice_to_excel.models import FileIssue, Invoice

from .conftest import requires_tesseract

SCANNED = "renovations_scanned.pdf"


def test_requested_language_is_used_when_installed() -> None:
    assert resolve_languages("spa", {"eng", "spa"}) == ("spa", None)
    assert resolve_languages("spa+eng", {"eng", "spa"}) == ("spa+eng", None)


def test_falls_back_to_english_with_warning() -> None:
    language, warning = resolve_languages("spa", {"eng", "osd"})
    assert language == "eng"
    assert warning is not None
    assert "brew install tesseract-lang" in warning


def test_partially_available_combination() -> None:
    language, warning = resolve_languages("spa+eng", {"eng"})
    assert language == "eng"
    assert warning is not None
    assert "spa" in warning


def test_no_usable_language_is_an_error() -> None:
    with pytest.raises(OcrUnavailableError):
        resolve_languages("spa", {"osd"})


def test_lines_are_rebuilt_from_word_positions() -> None:
    # Tesseract put the label and its amount in different blocks; they share a baseline.
    data: dict[str, list[Any]] = {
        "text": ["100,00", "Fecha:", "", "Total", "01/02/2026"],
        "conf": ["90", "80", "-1", "96", "85"],
        "left": [400, 50, 0, 50, 200],
        "top": [102, 20, 0, 100, 22],
        "height": [20, 20, 0, 22, 20],
    }
    text, confidences = _lines_from_data(data)
    assert text == "Fecha: 01/02/2026\nTotal 100,00"
    assert sorted(confidences) == [80.0, 85.0, 90.0, 96.0]


@requires_tesseract
def test_engine_reads_scanned_pdf(invoice_folder: Path) -> None:
    document = OcrEngine("spa").read(invoice_folder / SCANNED)
    assert document.ocr
    assert document.ocr_confidence is not None
    assert document.ocr_confidence > 60
    assert "Factura" in document.text
    assert "4.162,40" in document.text


@requires_tesseract
def test_scanned_invoice_goes_through_ocr_and_validation(invoice_folder: Path) -> None:
    outcome = build_pipeline(Settings(_env_file=None)).process_pdf(invoice_folder / SCANNED)
    assert isinstance(outcome, Invoice)
    assert outcome.ocr
    assert outcome.supplier == "Reformas Escaneadas, S.L."
    assert outcome.tax_id == "B55443329"
    assert outcome.number == "R-2026-05"
    assert outcome.invoice_date == date(2026, 3, 2)
    assert outcome.net_amount == Decimal("3440.00")
    assert outcome.total == Decimal("4162.40")
    assert outcome.is_ok
    assert 0.5 < outcome.overall_confidence < 0.95  # OCR output is never fully trusted


@requires_tesseract
def test_ocr_can_be_disabled(invoice_folder: Path) -> None:
    pipeline = build_pipeline(Settings(_env_file=None, ocr_enabled=False))
    outcome = pipeline.process_pdf(invoice_folder / SCANNED)
    assert isinstance(outcome, FileIssue)
    assert "contains no text" in outcome.reason


def test_missing_tesseract_becomes_a_file_issue(
    invoice_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    settings = Settings(_env_file=None, tesseract_cmd="/nonexistent/tesseract")
    pipeline = Pipeline(ocr=OcrReader(settings))
    outcome = pipeline.process_pdf(invoice_folder / SCANNED)
    assert isinstance(outcome, FileIssue)
    assert "OCR is unavailable" in outcome.reason


def test_engine_rejects_unusable_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    with pytest.raises(OcrUnavailableError):
        OcrEngine("spa", tesseract_cmd="/nonexistent/tesseract")

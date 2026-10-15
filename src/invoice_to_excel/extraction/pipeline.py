"""Orchestration: text layer or OCR, rule-based and/or LLM extraction, validation, scoring."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from invoice_to_excel.config import ExtractorMode, Settings
from invoice_to_excel.extraction.base import (
    ConfigurationError,
    DocumentText,
    ExtractionError,
    Extractor,
)
from invoice_to_excel.extraction.llm import LlmClient, LlmExtractor, create_anthropic_client
from invoice_to_excel.extraction.ocr import OcrEngine, OcrUnavailableError
from invoice_to_excel.extraction.rules import RuleBasedExtractor
from invoice_to_excel.extraction.text import MIN_CHARS, read_pdf_text
from invoice_to_excel.models import FileIssue, Invoice, Result
from invoice_to_excel.validation import (
    adjust_confidence,
    flag_duplicates,
    ocr_factor,
    scale_confidence,
    validate_invoice,
)

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, Path, Invoice | FileIssue], None]


class OcrReader:
    """Lazy OCR so that text PDFs never load Tesseract."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._engine: OcrEngine | None = None

    def read(self, path: Path) -> DocumentText:
        if self._engine is None:
            self._engine = OcrEngine(
                self._settings.ocr_language, self._settings.ocr_dpi, self._settings.tesseract_cmd
            )
        return self._engine.read(path)


class Pipeline:
    """Turns PDFs into validated, scored invoices. `process_pdf` never raises."""

    def __init__(
        self,
        *,
        mode: ExtractorMode = "rules",
        rules: Extractor | None = None,
        llm: Extractor | None = None,
        ocr: OcrReader | None = None,
        confidence_threshold: float = 0.7,
    ):
        if mode in ("llm", "auto") and llm is None:
            raise ConfigurationError(f"Extractor mode '{mode}' needs an LLM extractor")
        self.mode = mode
        self._rules = rules or RuleBasedExtractor()
        self._llm = llm
        self._ocr = ocr
        self._threshold = confidence_threshold

    # -- single document -------------------------------------------------
    def process_pdf(self, path: Path) -> Invoice | FileIssue:
        outcome = self._process(path)
        if isinstance(outcome, FileIssue):
            log.warning("file skipped", extra={"file": path.name, "reason": outcome.reason})
        else:
            log.info(
                "invoice processed",
                extra={
                    "file": path.name,
                    "status": "ok" if outcome.is_ok else "review",
                    "source": outcome.source,
                    "ocr": outcome.ocr,
                    "confidence": outcome.overall_confidence,
                },
            )
        return outcome

    def _process(self, path: Path) -> Invoice | FileIssue:
        try:
            document = self._read(path)
        except OcrUnavailableError as exc:
            return FileIssue(
                filename=path.name,
                reason=f"The PDF contains no text and OCR is unavailable ({exc})",
            )
        except Exception as exc:  # corrupt or password-protected PDF, OCR failure...
            return FileIssue(
                filename=path.name,
                reason=f"Could not open/read the PDF ({type(exc).__name__}: {exc})",
            )
        if len(document.text.strip()) < MIN_CHARS:
            return FileIssue(
                filename=path.name,
                reason="No readable text found, even after OCR"
                if document.ocr
                else "The PDF contains no text (scanned document or image?). "
                "Needs OCR or manual review",
            )
        try:
            invoice = self._extract(document, path.name)
        except ExtractionError as exc:
            return FileIssue(filename=path.name, reason=f"Error while extracting data ({exc})")
        except Exception as exc:
            return FileIssue(
                filename=path.name,
                reason=f"Error while extracting data ({type(exc).__name__}: {exc})",
            )
        if invoice.net_amount is None and invoice.total is None and not invoice.number:
            return FileIssue(
                filename=path.name,
                reason="Not recognised as an invoice: no amounts or invoice number found",
            )
        return invoice

    def _read(self, path: Path) -> DocumentText:
        text = read_pdf_text(path)
        if len(text.strip()) >= MIN_CHARS or self._ocr is None:
            return DocumentText(text)
        log.info("no text layer, running OCR", extra={"file": path.name})
        return self._ocr.read(path)

    def _extract(self, document: DocumentText, filename: str) -> Invoice:
        if self.mode == "llm":
            return self._finish(self._llm_extractor().extract(document.text, filename), document)
        candidate = self._finish(self._rules.extract(document.text, filename), document)
        if self.mode == "rules" or not self._needs_fallback(candidate):
            return candidate
        log.info("rule-based result incomplete, asking the LLM", extra={"file": filename})
        try:
            extracted = self._llm_extractor().extract(document.text, filename)
            alternative = self._finish(extracted, document)
        except Exception as exc:
            log.warning("llm fallback failed", extra={"file": filename, "error": str(exc)})
            return candidate
        return alternative if _rank(alternative) > _rank(candidate) else candidate

    def _llm_extractor(self) -> Extractor:
        if self._llm is None:
            raise ConfigurationError("LLM extractor is not configured")
        return self._llm

    def _needs_fallback(self, invoice: Invoice) -> bool:
        return bool(invoice.missing_fields) or invoice.overall_confidence < self._threshold

    @staticmethod
    def _finish(invoice: Invoice, document: DocumentText) -> Invoice:
        invoice.ocr = document.ocr
        if document.ocr:
            scale_confidence(invoice, ocr_factor(document.ocr_confidence))
        validate_invoice(invoice)
        adjust_confidence(invoice)
        return invoice

    # -- folders ---------------------------------------------------------
    def process_folder(
        self,
        folder: Path,
        recursive: bool = False,
        progress: ProgressCallback | None = None,
    ) -> Result:
        pdfs = list_pdfs(folder, recursive)
        result = Result()
        for i, pdf in enumerate(pdfs, 1):
            outcome = self.process_pdf(pdf)
            if isinstance(outcome, FileIssue):
                result.file_issues.append(outcome)
            else:
                result.invoices.append(outcome)
            if progress:
                progress(i, len(pdfs), pdf, outcome)
        flag_duplicates(result.invoices)
        return result


def _rank(invoice: Invoice) -> tuple[bool, float]:
    return (not invoice.problems and not invoice.missing_fields, invoice.overall_confidence)


def list_pdfs(folder: Path, recursive: bool = False) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(p for p in folder.glob(pattern) if p.is_file() and p.suffix.lower() == ".pdf")


def build_pipeline(
    settings: Settings,
    mode: ExtractorMode | None = None,
    llm_client: LlmClient | None = None,
) -> Pipeline:
    """Create a pipeline from settings.

    The LLM extractor sends invoice text to a third party, so it is only built when
    `settings.llm_enabled` is true, whatever mode is requested.
    """
    mode = mode or settings.extractor
    llm: Extractor | None = None
    if mode in ("llm", "auto"):
        if not settings.llm_enabled:
            raise ConfigurationError(
                f"Extractor '{mode}' sends invoice text to the Anthropic API and is disabled. "
                "Set INVOICE_LLM_ENABLED=true to opt in, or use --extractor rules."
            )
        client = llm_client
        if client is None:
            key = settings.anthropic_api_key
            client = create_anthropic_client(key.get_secret_value() if key else None)
        llm = LlmExtractor(client, settings.llm_model, settings.llm_max_tokens)
        log.warning(
            "invoice text will be sent to the Anthropic API",
            extra={"model": settings.llm_model, "mode": mode},
        )
    ocr = OcrReader(settings) if settings.ocr_enabled else None
    return Pipeline(mode=mode, llm=llm, ocr=ocr, confidence_threshold=settings.confidence_threshold)

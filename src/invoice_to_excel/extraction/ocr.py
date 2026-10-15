"""OCR for scanned PDFs: pypdfium2 renders the pages, Tesseract reads them.

Requires the `ocr` extra and a Tesseract binary. The configured language is used when
its data is installed; otherwise English is used with a warning, since digits, tax IDs
and most invoice labels survive that fallback with reduced accuracy.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import Any

from invoice_to_excel.extraction.base import ConfigurationError, DocumentText

log = logging.getLogger(__name__)

FALLBACK_LANGUAGE = "eng"


class OcrUnavailableError(ConfigurationError):
    """OCR cannot run: missing Python dependencies or Tesseract binary."""


def resolve_languages(requested: str, available: Collection[str]) -> tuple[str, str | None]:
    """Choose the Tesseract language string and, when degraded, a warning.

    `requested` may combine languages with '+', e.g. 'spa+eng'. Missing ones are
    dropped; if none remain the English model is used.
    """
    wanted = [lang for lang in requested.split("+") if lang]
    usable = [lang for lang in wanted if lang in available]
    if len(usable) == len(wanted) and usable:
        return "+".join(usable), None
    if usable:
        missing = sorted(set(wanted) - set(usable))
        return "+".join(usable), f"Tesseract language data missing for: {', '.join(missing)}"
    if FALLBACK_LANGUAGE in available:
        return FALLBACK_LANGUAGE, (
            f"Tesseract language '{requested}' is not installed; falling back to "
            f"'{FALLBACK_LANGUAGE}' (accuracy on Spanish text will be lower). "
            "Install it, e.g. `brew install tesseract-lang` or `apt install tesseract-ocr-spa`"
        )
    raise OcrUnavailableError(
        f"Tesseract has neither '{requested}' nor '{FALLBACK_LANGUAGE}' language data installed"
    )


class OcrEngine:
    """Reads image-only PDFs. Dependencies are imported lazily so the extra stays optional."""

    def __init__(self, language: str = "spa", dpi: int = 300, tesseract_cmd: str | None = None):
        try:
            import pypdfium2
            import pytesseract
        except ImportError as exc:
            raise OcrUnavailableError(
                "OCR needs the 'ocr' extra: pip install 'invoice-pdf-to-excel[ocr]'"
            ) from exc
        self._pdfium: Any = pypdfium2
        self._tesseract: Any = pytesseract
        self._dpi = dpi
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        elif not shutil.which("tesseract"):
            raise OcrUnavailableError(
                "Tesseract binary not found on PATH "
                "(brew install tesseract / apt install tesseract-ocr)"
            )
        try:
            available = set(pytesseract.get_languages(config=""))
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailableError(f"Tesseract is not usable: {exc}") from exc
        self.language, warning = resolve_languages(language, available)
        if warning:
            log.warning(warning, extra={"language": self.language})

    def read(self, path: Path) -> DocumentText:
        """OCR every page; confidence is Tesseract's mean word confidence (0-100)."""
        scale = self._dpi / 72
        pdf = self._pdfium.PdfDocument(path)
        pages: list[str] = []
        confidences: list[float] = []
        try:
            for index in range(len(pdf)):
                image = pdf[index].render(scale=scale).to_pil().convert("L")
                data = self._tesseract.image_to_data(
                    image, lang=self.language, output_type=self._tesseract.Output.DICT
                )
                text, conf = _lines_from_data(data)
                pages.append(text)
                confidences.extend(conf)
        finally:
            pdf.close()
        mean = sum(confidences) / len(confidences) if confidences else None
        return DocumentText("\n".join(pages), ocr=True, ocr_confidence=mean)


def _lines_from_data(data: dict[str, list[Any]]) -> tuple[str, list[float]]:
    """Rebuild text lines from Tesseract's word table and collect word confidences.

    Words are grouped by vertical position rather than by Tesseract's own line ids:
    its layout analysis often splits a label and its amount into separate blocks, and
    the extraction rules rely on both being on one line, as in the PDF text layer.
    """
    words: list[tuple[float, float, int, str]] = []  # (centre y, height, left, text)
    confidences: list[float] = []
    for i, raw in enumerate(data["text"]):
        token = str(raw).strip()
        conf = float(data["conf"][i])
        if not token or conf < 0:
            continue
        top, height = int(data["top"][i]), int(data["height"][i])
        words.append((top + height / 2, float(height), int(data["left"][i]), token))
        confidences.append(conf)

    lines: list[list[tuple[float, float, int, str]]] = []
    for word in sorted(words):
        if lines:
            centre = sum(w[0] for w in lines[-1]) / len(lines[-1])
            typical_height = sorted(w[1] for w in lines[-1])[len(lines[-1]) // 2]
            if abs(word[0] - centre) <= 0.5 * typical_height:
                lines[-1].append(word)
                continue
        lines.append([word])
    text = "\n".join(" ".join(w[3] for w in sorted(line, key=lambda w: w[2])) for line in lines)
    return text, confidences

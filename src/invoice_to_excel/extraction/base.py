"""Extraction interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from invoice_to_excel.models import Invoice


class ExtractionError(Exception):
    """An extractor could not produce an invoice from the given text."""


class ConfigurationError(Exception):
    """The requested extraction setup is unavailable or not permitted by the configuration."""


@dataclass(frozen=True)
class DocumentText:
    """Text of a PDF and how it was obtained."""

    text: str
    ocr: bool = False
    ocr_confidence: float | None = None


class Extractor(ABC):
    """Turns invoice text into an `Invoice`. Implementations must not validate."""

    name: str = "extractor"

    @abstractmethod
    def extract(self, text: str, filename: str) -> Invoice: ...

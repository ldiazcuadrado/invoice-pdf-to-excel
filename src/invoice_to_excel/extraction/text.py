"""Embedded text extraction from PDF files."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

MIN_CHARS = 25  # below this a PDF is considered to hold no text layer


def read_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)

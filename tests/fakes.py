"""Test doubles for the LLM client."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from invoice_to_excel.extraction.base import Extractor
from invoice_to_excel.extraction.llm import TOOL_NAME
from invoice_to_excel.models import Invoice

GOOD_PAYLOAD: dict[str, Any] = {
    "supplier": "Suministros Ficticios, S.L.",
    "tax_id": "B-87654323",
    "number": "2026-001",
    "invoice_date": "2026-01-15",
    "tax_lines": [{"base": 252.6, "rate": 21, "vat": 53.05}],
    "irpf": None,
    "total": 305.65,
    "field_confidence": {"supplier": 0.99, "tax_id": 0.99, "number": 0.95, "total": 0.97},
}


class FakeMessages:
    def __init__(self, payload: Any = None, error: Exception | None = None, block: Any = None):
        self.payload = payload
        self.error = error
        self.block = block
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.block is not None:
            return SimpleNamespace(content=[self.block])
        block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input=self.payload)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok"), block])


class FakeClient:
    def __init__(self, payload: Any = None, error: Exception | None = None, block: Any = None):
        self.messages = FakeMessages(payload, error, block)


class PartialExtractor(Extractor):
    """Stands in for a rule-based extractor that only found a supplier and a number."""

    name = "partial"

    def extract(self, text: str, filename: str) -> Invoice:
        return Invoice(
            filename=filename,
            supplier="Someone",
            number="X-9",
            confidence={"supplier": 0.5, "number": 0.5},
        )

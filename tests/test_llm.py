from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from invoice_to_excel.extraction.base import ConfigurationError, ExtractionError
from invoice_to_excel.extraction.llm import (
    MAX_LLM_CONFIDENCE,
    MAX_TEXT_CHARS,
    SYSTEM_PROMPT,
    TOOL,
    TOOL_NAME,
    LlmExtractor,
    create_anthropic_client,
)

from .fakes import GOOD_PAYLOAD, FakeClient

MODEL = "claude-sonnet-5-5"


def test_extracts_fields_from_tool_call() -> None:
    client = FakeClient(GOOD_PAYLOAD)
    invoice = LlmExtractor(client, MODEL).extract("invoice text", "a.pdf")
    assert invoice.source == "llm"
    assert invoice.supplier == "Suministros Ficticios, S.L."
    assert invoice.tax_id == "B87654323"  # normalised
    assert invoice.invoice_date == date(2026, 1, 15)
    assert invoice.net_amount == Decimal("252.6")
    assert invoice.total == Decimal("305.65")
    assert invoice.tax_lines[0].rate == Decimal(21)


def test_request_is_a_forced_tool_call() -> None:
    client = FakeClient(GOOD_PAYLOAD)
    LlmExtractor(client, MODEL, max_tokens=512).extract("invoice text", "a.pdf")
    (call,) = client.messages.calls
    assert call["model"] == MODEL
    assert call["max_tokens"] == 512
    assert call["tools"] == [TOOL]
    assert call["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert call["system"] == SYSTEM_PROMPT
    assert "<invoice>\ninvoice text\n</invoice>" in call["messages"][0]["content"]


def test_tool_schema_is_valid_json_schema_shape() -> None:
    schema = TOOL["input_schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) <= set(schema["properties"])
    assert "tax_lines" in schema["properties"]


def test_text_is_truncated() -> None:
    client = FakeClient(GOOD_PAYLOAD)
    LlmExtractor(client, MODEL).extract("x" * (MAX_TEXT_CHARS * 2), "a.pdf")
    content = client.messages.calls[0]["messages"][0]["content"]
    assert len(content) < MAX_TEXT_CHARS + 50


def test_self_reported_confidence_is_capped() -> None:
    invoice = LlmExtractor(FakeClient(GOOD_PAYLOAD), MODEL).extract("t", "a.pdf")
    assert invoice.confidence["supplier"] == MAX_LLM_CONFIDENCE
    assert invoice.confidence["number"] == MAX_LLM_CONFIDENCE
    payload = GOOD_PAYLOAD | {"field_confidence": {"supplier": 0.2}}
    low = LlmExtractor(FakeClient(payload), MODEL).extract("t", "a.pdf")
    assert low.confidence["supplier"] == 0.2
    assert low.confidence["number"] == 0.8  # not reported: default


def test_nulls_and_empty_lines_are_tolerated() -> None:
    payload: dict[str, Any] = {
        "supplier": "",
        "tax_id": None,
        "tax_lines": [{"base": None, "rate": None, "vat": None}],
        "irpf": -15,
    }
    invoice = LlmExtractor(FakeClient(payload), MODEL).extract("t", "a.pdf")
    assert invoice.supplier is None
    assert invoice.tax_lines == []
    assert invoice.irpf == Decimal(15)


def test_json_string_input_is_accepted() -> None:
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME, input='{"supplier": "S, S.L."}')
    invoice = LlmExtractor(FakeClient(block=block), MODEL).extract("t", "a.pdf")
    assert invoice.supplier == "S, S.L."


def test_missing_tool_call_is_an_error() -> None:
    block = SimpleNamespace(type="text", text="I cannot do that")
    with pytest.raises(ExtractionError, match="tool call"):
        LlmExtractor(FakeClient(block=block), MODEL).extract("t", "a.pdf")


def test_invalid_payload_is_an_error() -> None:
    with pytest.raises(ExtractionError, match="invalid payload"):
        LlmExtractor(FakeClient({"invoice_date": "not a date"}), MODEL).extract("t", "a.pdf")


def test_client_errors_propagate() -> None:
    with pytest.raises(TimeoutError):
        LlmExtractor(FakeClient(error=TimeoutError()), MODEL).extract("t", "a.pdf")


def test_real_client_requires_a_key() -> None:
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        create_anthropic_client(None)


def test_real_client_is_built_without_network() -> None:
    client = create_anthropic_client("sk-test-not-a-real-key")
    assert hasattr(client, "messages")

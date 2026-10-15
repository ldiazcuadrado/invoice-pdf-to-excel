import logging
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from invoice_to_excel.config import Settings
from invoice_to_excel.extraction import ConfigurationError, Extractor, Pipeline, build_pipeline
from invoice_to_excel.extraction.llm import LlmExtractor
from invoice_to_excel.models import FileIssue, Invoice

from .fakes import GOOD_PAYLOAD, FakeClient, PartialExtractor

GOOD_FILE = "supplies_2026-001.pdf"


def llm_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, llm_enabled=True, **overrides)  # type: ignore[arg-type]


def test_textless_pdf_without_ocr_becomes_issue(invoice_folder: Path) -> None:
    pipeline = Pipeline()
    outcome = pipeline.process_pdf(invoice_folder / "renovations_scanned.pdf")
    assert isinstance(outcome, FileIssue)
    assert "contains no text" in outcome.reason


def test_corrupt_pdf_does_not_crash(tmp_path: Path, pipeline: Pipeline) -> None:
    (tmp_path / "broken.pdf").write_bytes(b"this is not a pdf")
    result = pipeline.process_folder(tmp_path)
    assert result.invoices == []
    assert result.file_issues[0].filename == "broken.pdf"
    assert "Could not open" in result.file_issues[0].reason


def test_non_invoice_pdf_becomes_issue(tmp_path: Path, pipeline: Pipeline) -> None:
    path = tmp_path / "letter.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 700, "Dear Sir, this is a plain letter that has no invoice data in it.")
    c.save()
    outcome = pipeline.process_pdf(path)
    assert isinstance(outcome, FileIssue)
    assert "Not recognised as an invoice" in outcome.reason


def test_pluggable_extractor(invoice_folder: Path) -> None:
    class Fixed(Extractor):
        def extract(self, text: str, filename: str) -> Invoice:
            return Invoice(filename=filename, number="X-1", total=Decimal(1))

    outcome = Pipeline(rules=Fixed()).process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert outcome.number == "X-1"
    assert outcome.problems  # validation always runs


def test_extractor_failure_becomes_issue(invoice_folder: Path) -> None:
    class Broken(Extractor):
        def extract(self, text: str, filename: str) -> Invoice:
            raise RuntimeError("boom")

    outcome = Pipeline(rules=Broken()).process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, FileIssue)
    assert "boom" in outcome.reason


def test_recursive_listing(invoice_folder: Path, tmp_path: Path, pipeline: Pipeline) -> None:
    import shutil

    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    shutil.copy(invoice_folder / GOOD_FILE, nested)
    assert pipeline.process_folder(tmp_path).invoices == []
    assert len(pipeline.process_folder(tmp_path, recursive=True).invoices) == 1


def test_progress_callback_and_duplicate_flagging(invoice_folder: Path, pipeline: Pipeline) -> None:
    seen: list[tuple[int, int, str]] = []
    result = pipeline.process_folder(
        invoice_folder, progress=lambda i, n, path, _: seen.append((i, n, path.name))
    )
    assert seen[0][:2] == (1, len(result.invoices) + len(result.file_issues))
    copy = next(i for i in result.invoices if i.filename == "supplies_2026-014_copy.pdf")
    assert copy.duplicate_of == "supplies_2026-014.pdf"


def test_processing_is_logged_with_structured_fields(
    invoice_folder: Path, pipeline: Pipeline, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="invoice_to_excel"):
        pipeline.process_pdf(invoice_folder / GOOD_FILE)
    record = next(r for r in caplog.records if r.getMessage() == "invoice processed")
    assert record.__dict__["file"] == GOOD_FILE
    assert record.__dict__["status"] == "ok"


# -- LLM modes ----------------------------------------------------------------


def test_llm_is_disabled_by_default() -> None:
    for mode in ("llm", "auto"):
        with pytest.raises(ConfigurationError, match="INVOICE_LLM_ENABLED"):
            build_pipeline(Settings(_env_file=None), mode)


def test_llm_needs_an_api_key() -> None:
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        build_pipeline(llm_settings(), "llm")


def test_api_key_comes_from_the_standard_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert llm_settings().anthropic_api_key is not None
    assert "sk-test" not in repr(llm_settings())


def test_llm_mode_always_uses_the_llm_and_validates(invoice_folder: Path) -> None:
    client = FakeClient(GOOD_PAYLOAD)
    pipeline = build_pipeline(llm_settings(), "llm", llm_client=client)
    outcome = pipeline.process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert outcome.source == "llm"
    assert len(client.messages.calls) == 1
    assert outcome.is_ok  # same validation as the rules path
    assert outcome.confidence["total"] >= 0.9


def test_llm_output_is_validated(invoice_folder: Path) -> None:
    bad = GOOD_PAYLOAD | {"total": 999.0, "tax_id": "B87654320"}
    pipeline = build_pipeline(llm_settings(), "llm", llm_client=FakeClient(bad))
    outcome = pipeline.process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert not outcome.is_ok
    assert any("does not add up" in p for p in outcome.problems)
    assert any("not valid" in p for p in outcome.problems)
    assert outcome.overall_confidence < 0.7


def test_llm_failure_becomes_issue(invoice_folder: Path) -> None:
    client = FakeClient(error=RuntimeError("api down"))
    outcome = build_pipeline(llm_settings(), "llm", llm_client=client).process_pdf(
        invoice_folder / GOOD_FILE
    )
    assert isinstance(outcome, FileIssue)
    assert "api down" in outcome.reason


def test_auto_does_not_call_the_llm_when_rules_are_complete(invoice_folder: Path) -> None:
    client = FakeClient(GOOD_PAYLOAD)
    pipeline = build_pipeline(llm_settings(), "auto", llm_client=client)
    outcome = pipeline.process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert outcome.source == "rules"
    assert client.messages.calls == []


def test_auto_falls_back_to_the_llm_when_rules_are_incomplete(invoice_folder: Path) -> None:
    client = FakeClient(GOOD_PAYLOAD)
    llm = LlmExtractor(client, "claude-sonnet-5-5")
    pipeline = Pipeline(mode="auto", rules=PartialExtractor(), llm=llm)
    outcome = pipeline.process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert outcome.source == "llm"
    assert outcome.is_ok
    assert len(client.messages.calls) == 1


def test_auto_keeps_rules_result_when_the_llm_fails(
    invoice_folder: Path, caplog: pytest.LogCaptureFixture
) -> None:
    llm = LlmExtractor(FakeClient(error=RuntimeError("api down")), "claude-sonnet-5-5")
    pipeline = Pipeline(mode="auto", rules=PartialExtractor(), llm=llm)
    with caplog.at_level(logging.WARNING, logger="invoice_to_excel"):
        outcome = pipeline.process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert outcome.source == "rules"
    assert any("llm fallback failed" in r.getMessage() for r in caplog.records)


def test_auto_keeps_rules_result_when_the_llm_is_not_better(invoice_folder: Path) -> None:
    llm = LlmExtractor(FakeClient({"supplier": "Only a name"}), "claude-sonnet-5-5")
    pipeline = Pipeline(mode="auto", rules=PartialExtractor(), llm=llm)
    outcome = pipeline.process_pdf(invoice_folder / GOOD_FILE)
    assert isinstance(outcome, Invoice)
    assert outcome.supplier == "Someone"


def test_low_confidence_triggers_the_fallback(invoice_folder: Path) -> None:
    # marta_design_03 is complete and valid but its supplier name is a weak guess (0.5);
    # with a strict threshold the LLM gets a second opinion.
    client = FakeClient(GOOD_PAYLOAD | {"number": "F-2026/003"})
    pipeline = build_pipeline(llm_settings(confidence_threshold=0.99), "auto", llm_client=client)
    pipeline.process_pdf(invoice_folder / "marta_design_03.pdf")
    assert len(client.messages.calls) == 1


def test_llm_modes_need_an_extractor() -> None:
    with pytest.raises(ConfigurationError):
        Pipeline(mode="auto")

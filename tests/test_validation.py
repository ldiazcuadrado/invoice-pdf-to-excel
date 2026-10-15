from datetime import date
from decimal import Decimal

from invoice_to_excel.models import Invoice, TaxLine
from invoice_to_excel.validation import (
    adjust_confidence,
    flag_duplicates,
    ocr_factor,
    scale_confidence,
    validate_invoice,
)

D = Decimal


def make_invoice(**overrides: object) -> Invoice:
    data: dict[str, object] = {
        "filename": "a.pdf",
        "supplier": "X",
        "tax_id": "B87654323",
        "number": "1",
        "invoice_date": date(2026, 1, 1),
        "tax_lines": [TaxLine(base=D("100"), rate=D("21"), vat=D("21.00"))],
        "total": D("121.00"),
    }
    data.update(overrides)
    return Invoice.model_validate(data)


def test_valid_invoice() -> None:
    assert validate_invoice(make_invoice()) == []


def test_missing_fields_are_reported() -> None:
    problems = validate_invoice(Invoice(filename="x.pdf"))
    assert len(problems) == 6
    assert "Supplier not found" in problems


def test_total_mismatch() -> None:
    problems = validate_invoice(make_invoice(total=D("131.00")))
    assert len(problems) == 1
    assert "does not add up" in problems[0]


def test_two_cent_tolerance() -> None:
    assert validate_invoice(make_invoice(total=D("121.02"))) == []
    assert validate_invoice(make_invoice(total=D("121.03")))


def test_irpf_is_subtracted() -> None:
    assert validate_invoice(make_invoice(irpf=D("15.00"), total=D("106.00"))) == []


def test_invalid_tax_id_is_flagged() -> None:
    assert any("not valid" in p for p in validate_invoice(make_invoice(tax_id="B87654320")))


def test_inconsistent_vat_amount() -> None:
    lines = [TaxLine(base=D("100"), rate=D("21"), vat=D("30.00"))]
    problems = validate_invoice(make_invoice(tax_lines=lines, total=D("130.00")))
    assert any("VAT amount" in p for p in problems)


def test_multiple_rates_validated_per_line_and_in_total() -> None:
    lines = [
        TaxLine(base=D("1000"), rate=D("21"), vat=D("210.00")),
        TaxLine(base=D("500"), rate=D("10"), vat=D("50.00")),
    ]
    assert validate_invoice(make_invoice(tax_lines=lines, total=D("1760.00"))) == []


def test_wrong_quota_on_one_of_several_lines() -> None:
    lines = [
        TaxLine(base=D("1000"), rate=D("21"), vat=D("210.00")),
        TaxLine(base=D("500"), rate=D("10"), vat=D("55.00")),
    ]
    problems = validate_invoice(make_invoice(tax_lines=lines, total=D("1765.00")))
    assert len(problems) == 1
    assert "10%" in problems[0]
    assert "expected 50.00" in problems[0]


def test_equivalence_surcharge_counts_towards_total() -> None:
    line = TaxLine(
        base=D("1000"), rate=D("21"), vat=D("210.00"), surcharge_rate=D("5.2"), surcharge=D("52.00")
    )
    assert validate_invoice(make_invoice(tax_lines=[line], total=D("1262.00"))) == []
    problems = validate_invoice(make_invoice(tax_lines=[line], total=D("1210.00")))
    assert "surcharge" in problems[0]


def test_wrong_surcharge_amount() -> None:
    line = TaxLine(
        base=D("1000"), rate=D("21"), vat=D("210.00"), surcharge_rate=D("5.2"), surcharge=D("60.00")
    )
    problems = validate_invoice(make_invoice(tax_lines=[line], total=D("1270.00")))
    assert len(problems) == 1
    assert "Equivalence surcharge" in problems[0]


def test_duplicates_are_flagged_and_idempotent() -> None:
    a, b = make_invoice(filename="a.pdf"), make_invoice(filename="b.pdf")
    other = make_invoice(filename="c.pdf", number="2")
    for _ in range(2):
        flag_duplicates([a, b, other])
        assert a.duplicate_of is None
        assert other.duplicate_of is None
        assert b.duplicate_of == "a.pdf"
    assert b.review_reasons == ["Possible duplicate invoice (same as a.pdf)"]
    assert not b.is_ok


def test_duplicate_flag_clears_when_original_is_gone() -> None:
    a, b = make_invoice(filename="a.pdf"), make_invoice(filename="b.pdf")
    flag_duplicates([a, b])
    flag_duplicates([b])
    assert b.duplicate_of is None


def test_duplicate_key_ignores_case_and_tax_id_format() -> None:
    a = make_invoice(filename="a.pdf", number="F-1")
    b = make_invoice(filename="b.pdf", number="f-1", tax_id="b-8765432-3")
    flag_duplicates([a, b])
    assert b.duplicate_of == "a.pdf"


# -- confidence -------------------------------------------------------------


def scored(**overrides: object) -> Invoice:
    invoice = make_invoice(
        confidence={"supplier": 0.85, "tax_id": 0.95, "number": 0.9, "invoice_date": 0.95}
        | {"net_amount": 0.6, "vat_amount": 0.6, "total": 0.6},
        **overrides,
    )
    validate_invoice(invoice)
    adjust_confidence(invoice)
    return invoice


def test_reconciled_amounts_raise_confidence() -> None:
    invoice = scored()
    assert invoice.confidence["net_amount"] == 0.9
    assert invoice.confidence["total"] == 0.9
    assert invoice.overall_confidence > 0.85


def test_failed_arithmetic_caps_amount_confidence() -> None:
    invoice = scored(total=D("131.00"))
    assert invoice.confidence["total"] <= 0.4
    assert invoice.overall_confidence < 0.7


def test_invalid_tax_id_caps_its_confidence() -> None:
    invoice = scored(tax_id="B87654320")
    assert invoice.confidence["tax_id"] <= 0.4


def test_missing_fields_score_zero() -> None:
    invoice = make_invoice(supplier=None, confidence={"supplier": 0.9})
    validate_invoice(invoice)
    adjust_confidence(invoice)
    assert invoice.confidence["supplier"] == 0.0
    assert set(invoice.low_confidence_fields(0.7)) >= {"supplier"}


def test_absent_irpf_has_no_score() -> None:
    assert "irpf" not in scored().confidence


def test_scale_confidence() -> None:
    invoice = make_invoice(confidence={"supplier": 1.0, "number": 0.5})
    scale_confidence(invoice, 0.8)
    assert invoice.confidence == {"supplier": 0.8, "number": 0.4}


def test_ocr_factor_bounds() -> None:
    assert ocr_factor(100) == 0.9
    assert ocr_factor(50) == 0.45
    assert ocr_factor(250) == 0.9
    assert ocr_factor(None) == 0.85

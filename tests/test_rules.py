from datetime import date
from decimal import Decimal
from pathlib import Path

from invoice_to_excel.extraction import RuleBasedExtractor
from invoice_to_excel.models import Invoice, Result

D = Decimal


def test_template_a(by_file: dict[str, Invoice]) -> None:
    f = by_file["supplies_2026-001.pdf"]
    assert f.supplier == "Suministros Ficticios, S.L."
    assert f.tax_id == "B87654323"
    assert f.number == "2026-001"
    assert f.invoice_date == date(2026, 1, 15)
    assert (f.net_amount, f.vat_amount, f.total) == (D("252.60"), D("53.05"), D("305.65"))
    assert [line.rate for line in f.tax_lines] == [D("21")]
    assert f.irpf is None
    assert f.is_ok


def test_template_b_irpf_and_written_date(by_file: dict[str, Invoice]) -> None:
    f = by_file["marta_design_03.pdf"]
    assert f.supplier == "Marta Ficticia Ruiz"
    assert f.tax_id == "12345678Z"
    assert f.number == "F-2026/003"
    assert f.invoice_date == date(2026, 3, 15)
    assert f.net_amount == D("1650.00")
    assert f.irpf == D("247.50")
    assert f.total == D("1749.00")
    assert f.is_ok


def test_template_c_reduced_vat_and_dashed_date(by_file: dict[str, Invoice]) -> None:
    f = by_file["stationery_F0031.pdf"]
    assert f.invoice_date == date(2026, 4, 3)
    assert f.tax_lines[0].rate == D("4")
    assert f.vat_amount == D("16.01")
    assert f.total == D("416.21")
    assert f.number == "2026/0031"
    assert f.is_ok


def test_template_d_summary_table(by_file: dict[str, Invoice]) -> None:
    f = by_file["workshop_A-1042.pdf"]
    assert f.tax_id == "B13579248"
    assert f.invoice_date == date(2026, 4, 28)
    assert (f.net_amount, f.vat_amount, f.total) == (D("1150.00"), D("241.50"), D("1391.50"))
    assert f.tax_lines[0].rate == D("21")
    assert f.is_ok


def test_two_rates_labelled_lines(by_file: dict[str, Invoice]) -> None:
    f = by_file["hardware_F-2026-0118.pdf"]
    assert [(line.rate, line.base, line.vat) for line in f.tax_lines] == [
        (D("21"), D("278.00"), D("58.38")),
        (D("10"), D("420.00"), D("42.00")),
    ]
    assert f.net_amount == D("698.00")
    assert f.vat_amount == D("100.38")
    assert f.total == D("798.38")
    assert f.is_ok


def test_three_rates(by_file: dict[str, Invoice]) -> None:
    f = by_file["stationery_F0077_multi.pdf"]
    assert {line.rate: (line.base, line.vat) for line in f.tax_lines} == {
        D("4"): (D("747.00"), D("29.88")),
        D("21"): (D("342.00"), D("71.82")),
        D("10"): (D("180.00"), D("18.00")),
    }
    assert f.total == D("1388.70")
    assert f.is_ok


def test_equivalence_surcharge_table(by_file: dict[str, Invoice]) -> None:
    f = by_file["wholesale_B-5521.pdf"]
    by_rate = {line.rate: line for line in f.tax_lines}
    assert by_rate[D("21")].surcharge_rate == D("5.2")
    assert by_rate[D("21")].surcharge == D("15.08")
    assert by_rate[D("10")].surcharge_rate == D("1.4")
    assert by_rate[D("10")].surcharge == D("6.05")
    assert f.surcharge_amount == D("21.13")
    assert f.total == D("847.23")
    assert f.is_ok


def test_single_rate_surcharge_table(by_file: dict[str, Invoice]) -> None:
    f = by_file["wholesale_B-5598.pdf"]
    assert len(f.tax_lines) == 1
    assert f.tax_lines[0].surcharge == D("17.78")
    assert f.is_ok


def test_customer_not_confused_with_issuer(result: Result) -> None:
    for f in result.invoices:
        assert f.tax_id != "B12345674"
        assert "Cliente" not in (f.supplier or "")


def test_mismatch_detected_in_pdf(by_file: dict[str, Invoice]) -> None:
    f = by_file["supplies_2026-031.pdf"]
    assert not f.is_ok
    assert f.total == D("489.16")
    assert "does not add up" in f.problems[0]


def test_only_expected_invoices_need_review(result: Result) -> None:
    assert sorted(f.filename for f in result.invoices if not f.is_ok) == [
        "supplies_2026-014_copy.pdf",
        "supplies_2026-031.pdf",
    ]


def test_field_confidence_reflects_how_values_were_found(by_file: dict[str, Invoice]) -> None:
    labelled = by_file["supplies_2026-001.pdf"]
    assert labelled.confidence["supplier"] == 0.85
    assert labelled.confidence["tax_id"] >= 0.95
    assert labelled.overall_confidence >= 0.9
    # An individual has no company suffix, so the name is a weaker guess.
    assert by_file["marta_design_03.pdf"].confidence["supplier"] == 0.5
    assert by_file["supplies_2026-031.pdf"].overall_confidence < 0.7


def test_vat_exempt_invoice() -> None:
    text = (
        "Servicios Exentos, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Base imponible 100,00 €\nTotal factura 100,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.tax_lines[0].rate == D(0)
    assert invoice.tax_lines[0].vat == D("0.00")


def test_vat_rate_is_derived_when_only_the_quota_is_printed() -> None:
    text = (
        "Servicios, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Base imponible 200,00 €\nCuota IVA 42,00 €\nTotal factura 242,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.tax_lines[0].rate == D("21")
    assert invoice.confidence["vat_amount"] == 0.6


def test_quota_is_derived_when_only_the_rate_is_printed() -> None:
    text = (
        "Servicios, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Base imponible 21%   200,00 €\nTotal factura 242,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.tax_lines[0].vat == D("42.00")


def test_total_vat_summary_line_is_not_double_counted() -> None:
    text = (
        "Servicios, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Base imponible 100,00 €\nIVA 21% 21,00 €\nTotal IVA 21,00 €\nTotal factura 121,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.vat_amount == D("21.00")
    assert len(invoice.tax_lines) == 1


def test_irpf_derived_from_percentage_only() -> None:
    text = (
        "Marta, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Base imponible 100,00 €\nIVA 21% 21,00 €\nRetención IRPF 15% 15,00 €\nTotal 106,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.irpf == D("15.00")


def test_subtotal_ignored_when_base_lines_exist() -> None:
    text = (
        "Servicios, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Subtotal 1.500,00 €\nBase imponible 21% 1.000,00 €\nIVA 21% 210,00 €\n"
        "Base imponible 10% 500,00 €\nIVA 10% 50,00 €\nTotal factura 1.760,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.net_amount == D("1500.00")
    assert len(invoice.tax_lines) == 2


def test_unmatched_quota_is_kept_and_flagged() -> None:
    text = (
        "Servicios, S.L.\nCIF: B87654323\nFactura nº X-1\nFecha: 01/02/2026\n"
        "Base imponible 21% 100,00 €\nIVA 21% 21,00 €\nIVA 10% 5,00 €\nTotal factura 121,00 €\n"
    )
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert len(invoice.tax_lines) == 2
    assert invoice.tax_lines[1].base is None


def test_supplier_label_and_unlabelled_date() -> None:
    text = "Proveedor: Taller Sin Sufijo\nNIF 12345678Z\nFactura nº 7\n03/04/2026\nTotal 10,00 €\n"
    invoice = RuleBasedExtractor().extract(text, "x.pdf")
    assert invoice.supplier == "Taller Sin Sufijo"
    assert invoice.invoice_date == date(2026, 4, 3)
    assert invoice.confidence["invoice_date"] == 0.6


def test_due_date_is_not_the_invoice_date() -> None:
    text = "Vencimiento: 30/04/2026\nFecha: 01/04/2026\n"
    assert RuleBasedExtractor().extract(text, "x.pdf").invoice_date == date(2026, 4, 1)


def test_extractor_on_garbage_yields_empty_invoice(tmp_path: Path) -> None:
    invoice = RuleBasedExtractor().extract("nothing useful here at all", "x.pdf")
    assert invoice.tax_lines == []
    assert invoice.total is None
    assert invoice.number is None

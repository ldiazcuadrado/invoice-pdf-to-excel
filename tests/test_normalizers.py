from datetime import date
from decimal import Decimal

import pytest

from invoice_to_excel.normalizers import (
    find_date,
    infer_rate,
    normalize_rate,
    parse_amount,
    parse_percentage,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.234,56 €", "1234.56"),
        ("1234,56", "1234.56"),
        ("€ 12,50", "12.50"),
        ("-150,00 €", "-150.00"),
        ("1.234.567,89", "1234567.89"),
        ("1,234.56", "1234.56"),
        ("12.50", "12.50"),
        ("1.234", "1234"),
        ("(45,10)", "-45.10"),
    ],
)
def test_parse_amount(text: str, expected: str) -> None:
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "abc", "€", "--"])
def test_parse_amount_invalid(text: str) -> None:
    assert parse_amount(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Fecha: 15/03/2026",
        "15-03-2026",
        "15.03.2026",
        "15/03/26",
        "15 de marzo de 2026",
        "15 Marzo 2026",
        "2026-03-15",
    ],
)
def test_find_date(text: str) -> None:
    assert find_date(text) == date(2026, 3, 15)


def test_abbreviated_september() -> None:
    assert find_date("1 sept. 2026") == date(2026, 9, 1)


def test_impossible_date() -> None:
    assert find_date("31/02/2026") is None


def test_no_date() -> None:
    assert find_date("sin fecha") is None


@pytest.mark.parametrize(("text", "expected"), [("21 %", "21"), ("5,2%", "5.2"), (" 10 ", "10")])
def test_parse_percentage(text: str, expected: str) -> None:
    assert parse_percentage(text) == Decimal(expected)


def test_parse_percentage_invalid() -> None:
    assert parse_percentage("abc") is None


@pytest.mark.parametrize(
    ("rate", "expected"),
    [("21.00", "21"), ("20", "20"), ("5.20", "5.2"), ("0.5", "0.5"), ("7.50", "7.5")],
)
def test_normalize_rate(rate: str, expected: str) -> None:
    result = normalize_rate(Decimal(rate))
    assert result == Decimal(expected)
    assert f"{result:f}" == expected


@pytest.mark.parametrize(
    ("base", "vat", "expected"),
    [
        ("100", "21", "21"),
        ("1650.00", "346.50", "21"),
        ("400.20", "16.01", "4"),
        ("200", "14", "7"),
        ("50", "0", "0"),
    ],
)
def test_infer_rate(base: str, vat: str, expected: str) -> None:
    assert infer_rate(Decimal(base), Decimal(vat)) == Decimal(expected)


def test_infer_rate_without_base() -> None:
    assert infer_rate(Decimal(0), Decimal(10)) is None

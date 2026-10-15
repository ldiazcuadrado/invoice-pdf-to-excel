import pytest

from invoice_to_excel.validation import (
    cif_check,
    dni_letter,
    is_valid_tax_id,
    normalize_tax_id,
    tax_id_kind,
)


@pytest.mark.parametrize(
    ("tax_id", "kind"),
    [
        ("12345678Z", "DNI"),
        ("X1234567L", "NIE"),
        ("Y1234567X", "NIE"),
        ("Z1234567R", "NIE"),
        ("B87654323", "CIF"),  # numeric control character
        ("A24681355", "CIF"),
        ("Q2826000H", "CIF"),  # public body: letter control
        ("b-8765432-3", "CIF"),  # hyphens and lower case
        ("ESB87654323", "CIF"),
        ("B 87654323", "CIF"),
    ],
)
def test_valid_tax_ids(tax_id: str, kind: str) -> None:
    assert tax_id_kind(tax_id) == kind
    assert is_valid_tax_id(tax_id)


@pytest.mark.parametrize(
    "tax_id",
    ["12345678A", "B87654320", "X1234567A", "1234", "", "Q28260001", "ES1234", "I1234567J"],
)
def test_invalid_tax_ids(tax_id: str) -> None:
    assert not is_valid_tax_id(tax_id)


def test_none_is_invalid() -> None:
    assert not is_valid_tax_id(None)


def test_normalize() -> None:
    assert normalize_tax_id(" es-b.8765432/3 ") == "B87654323"


def test_dni_letter() -> None:
    assert dni_letter(12345678) == "Z"
    assert dni_letter(0) == "T"


def test_cif_check_returns_digit_and_letter() -> None:
    assert cif_check("8765432") == ("3", "C")


@pytest.mark.parametrize(
    ("tax_id", "control_is_letter"),
    [
        ("K0000000" + "J", True),  # K: letter control
        ("A00000000", False),  # A: digit control
    ],
)
def test_control_character_class(tax_id: str, control_is_letter: bool) -> None:
    # '0000000' hashes to digit 0 / letter J, so a digit control is rejected for K and vice versa
    assert is_valid_tax_id(tax_id)
    swapped = tax_id[:-1] + ("0" if control_is_letter else "J")
    assert not is_valid_tax_id(swapped)

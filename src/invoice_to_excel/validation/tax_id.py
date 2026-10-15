"""Spanish tax identifiers: DNI, NIE and CIF check characters."""

from __future__ import annotations

import re

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETTER_CONTROL = "KPQRSNW"  # the control character is always a letter
_CIF_DIGIT_CONTROL = "ABEH"  # the control character is always a digit
_CONTROL_LETTERS = "JABCDEFGHI"


def normalize_tax_id(value: str) -> str:
    v = re.sub(r"[\s.\-/]", "", value.upper())
    if v.startswith("ES") and len(v) == 11:
        v = v[2:]
    return v


def dni_letter(number: int) -> str:
    return _DNI_LETTERS[number % 23]


def cif_check(digits: str) -> tuple[str, str]:
    """Return the (digit, letter) control pair for the 7 middle digits of a CIF."""
    even_sum = sum(int(d) for d in digits[1::2])
    odd_sum = 0
    for d in digits[0::2]:
        doubled = int(d) * 2
        odd_sum += doubled // 10 + doubled % 10
    digit = (10 - (even_sum + odd_sum) % 10) % 10
    return str(digit), _CONTROL_LETTERS[digit]


def tax_id_kind(value: str) -> str | None:
    """Return 'DNI', 'NIE' or 'CIF' for a well-formed ID with a correct control character."""
    v = normalize_tax_id(value)
    if re.fullmatch(r"\d{8}[A-Z]", v):
        return "DNI" if dni_letter(int(v[:8])) == v[8] else None
    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", v):
        number = int(str("XYZ".index(v[0])) + v[1:8])
        return "NIE" if dni_letter(number) == v[8] else None
    if re.fullmatch(r"[ABCDEFGHJKLMNPQRSUVW]\d{7}[0-9A-J]", v):
        digit, letter = cif_check(v[1:8])
        control = v[8]
        if v[0] in _CIF_LETTER_CONTROL:
            ok = control == letter
        elif v[0] in _CIF_DIGIT_CONTROL:
            ok = control == digit
        else:
            ok = control in (digit, letter)
        return "CIF" if ok else None
    return None


def is_valid_tax_id(value: str | None) -> bool:
    return bool(value) and tax_id_kind(value or "") is not None

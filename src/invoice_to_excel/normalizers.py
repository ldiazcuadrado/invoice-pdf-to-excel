"""Normalisation of amounts and dates written in Spanish format."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

MONTHS_ES = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

# Amount with decimals (1.234,56 | 1234,56 | 1234.56 | -150,00)
AMOUNT_RE = r"[-\u2212]?\s?(?:\d{1,3}(?:\.\d{3})+|\d+)[,.]\d{2}(?!\d)"


def parse_amount(text: str) -> Decimal | None:
    """Convert '1.234,56 €' (or variants) to Decimal. None if it is not an amount."""
    s = text.strip()
    for junk in ("€", "EUR", "eur", "\xa0", " "):
        s = s.replace(junk, "")
    s = s.replace("\u2212", "-")
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()+")
    if not s or not re.fullmatch(r"[\d.,]+", s) or not any(c.isdigit() for c in s):
        return None

    last_dot, last_comma = s.rfind("."), s.rfind(",")
    dec: str | None
    if last_dot >= 0 and last_comma >= 0:
        # Whichever separator appears last is the decimal one.
        dec = "," if last_comma > last_dot else "."
    elif last_comma >= 0:
        dec = "," if s.count(",") == 1 else None
    elif last_dot >= 0:
        # '1.234' -> thousands (Spanish format); '12.50' -> decimal
        after = len(s) - last_dot - 1
        dec = "." if s.count(".") == 1 and after != 3 else None
    else:
        dec = None

    if dec is None:
        cleaned = s.replace(".", "").replace(",", "")
    else:
        whole, _, frac = s.rpartition(dec)
        cleaned = whole.replace(".", "").replace(",", "") + "." + frac
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def _safe_date(d: int, m: int, a: int) -> date | None:
    if a < 100:
        a += 2000
    try:
        return date(a, m, d)
    except ValueError:
        return None


_DATE_TEXT = re.compile(
    r"\b(\d{1,2})\s*(?:de\s+)?([a-záéíóú]{3,10})\.?\s*(?:de|del)?\s*(\d{4})\b", re.I
)
_DATE_NUM = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4}|\d{2})\b")
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def find_date(text: str) -> date | None:
    """Return the first recognisable date: dd/mm/yyyy, dd-mm-yyyy, dd.mm.yyyy,
    yyyy-mm-dd or '15 de marzo de 2026'."""
    candidates: list[tuple[int, date]] = []
    for m in _DATE_TEXT.finditer(text):
        month = MONTHS_ES.get(m.group(2)[:3].lower().replace("é", "e"))
        if month and (f := _safe_date(int(m.group(1)), month, int(m.group(3)))):
            candidates.append((m.start(), f))
    for m in _DATE_ISO.finditer(text):
        if f := _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1))):
            candidates.append((m.start(), f))
    for m in _DATE_NUM.finditer(text):
        if f := _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            candidates.append((m.start(), f))
    if not candidates:
        return None
    return min(candidates, key=lambda c: c[0])[1]


def parse_percentage(text: str) -> Decimal | None:
    s = text.strip().replace("%", "").replace(",", ".").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


STANDARD_VAT_RATES = (Decimal(0), Decimal(4), Decimal(10), Decimal(21))
SURCHARGE_FOR_RATE = {
    Decimal(21): Decimal("5.2"),
    Decimal(10): Decimal("1.4"),
    Decimal(4): Decimal("0.5"),
}


def normalize_rate(rate: Decimal) -> Decimal:
    """Drop a meaningless fractional part: Decimal('21.00') -> Decimal('21')."""
    if rate == rate.to_integral_value():
        return rate.quantize(Decimal(1))
    return rate.quantize(Decimal("0.01")).normalize()


def infer_rate(base: Decimal, vat: Decimal) -> Decimal | None:
    """Rate implied by a base and a quota, snapped to a statutory rate when it is close."""
    if not base:
        return None
    implied = vat / base * 100
    for standard in STANDARD_VAT_RATES:
        if abs(implied - standard) <= Decimal("0.3"):
            return standard
    return normalize_rate(implied.quantize(Decimal("0.1")))

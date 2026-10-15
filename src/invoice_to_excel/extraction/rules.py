"""Rule-based extraction for common layouts of Spanish invoices.

Labels and formats are regular expressions tolerant to layout variations ("Base
imponible", "Total factura", "Cuota IVA", "Nº Factura"...). To support a new layout,
extend the patterns below or subclass `RuleBasedExtractor` for a supplier-specific
override; see "Adding rules" in the README.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from invoice_to_excel.extraction.base import Extractor
from invoice_to_excel.models import Invoice, TaxLine
from invoice_to_excel.normalizers import (
    AMOUNT_RE,
    SURCHARGE_FOR_RATE,
    find_date,
    infer_rate,
    normalize_rate,
    parse_amount,
    parse_percentage,
)
from invoice_to_excel.validation.tax_id import is_valid_tax_id, normalize_tax_id

_I = re.I
_AMT = f"({AMOUNT_RE})"
_PCT = r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%"
_NO_NUM = r"[^\d\n\-\u2212]"

_TAX_ID_RE = re.compile(
    r"(?<![A-Z0-9])(?:ES[-\s]?)?("
    r"[XYZ][-.]?\d{7}[-.]?[A-Z]|\d{8}[-.]?[A-Z]|[ABCDEFGHJKLMNPQRSUVW][-.]?\d{7}[-.]?[0-9A-J]"
    r")(?![A-Z0-9])",
    _I,
)
_TAX_ID_LABEL = re.compile(r"\b(?:N\.?I\.?F|C\.?I\.?F|DNI|NIE|VAT)\b", _I)
_CUSTOMER_SECTION = re.compile(
    r"^\s*(?:datos\s+del\s+)?(?:cliente|facturar\s+a|destinatario|bill\s+to)\b", _I
)
_ISSUER_SECTION = re.compile(r"^\s*(?:datos\s+del\s+)?(?:emisor|proveedor|expedidor)\b", _I)
_COMPANY_SUFFIX = re.compile(
    r"\b(?:S\.?\s?L\.?\s?U?\.?|S\.?\s?A\.?|S\.?\s?L\.?\s?P\.?|S\.?\s?C\.?|C\.?\s?B\.?)\s*$|"
    r"\b(?:S\.L\.|S\.A\.|S\.L\.U\.|S\.L\.P\.|S\.C\.)",
    _I,
)
_TITLES = re.compile(r"^\s*(?:factura|invoice|duplicado|original)\b", _I)
_SUPPLIER_LABEL = re.compile(
    r"^\s*(?:emisor|proveedor|raz[óo]n\s+social|expedidor)\s*:\s*(.+?)\s*$", _I
)

_NUMBER_RE = re.compile(
    r"(?:n[úu]mero\s+de\s+factura|n[úu]m(?:ero|\.)?\s*(?:de\s+)?factura|"
    r"n[º°o*]\.?\s*(?:de\s+)?factura|factura\s*(?:n[º°o*]|n[úu]m(?:ero)?)\.?|"
    r"fra\.?\s*(?:n[º°o*]|n[úu]m)\.?|invoice\s*(?:no\.?|number|#))"
    r"\s*[:.]?\s*([A-Z0-9][A-Z0-9\-/._]*\d[A-Z0-9\-/._]*)",
    _I,
)
_DATE_LABEL = re.compile(r"fecha(?:\s+de)?(?:\s+(?:emisi[óo]n|factura|expedici[óo]n))?", _I)
_DUE_DATE = re.compile(r"vencim|pago|vto", _I)

_NET_RE = re.compile(
    rf"(?<![a-z])(?P<label>base\s+imponible|base|subtotal|importe\s+neto|neto)"
    rf"(?:\s*\(?{_PCT}\)?)?{_NO_NUM}{{0,25}}{_AMT}",
    _I,
)
_TOTAL_RE = re.compile(
    rf"^\s*(?:total\s+factura|total\s+a\s+pagar|importe\s+total|"
    rf"total(?!\s+(?:iva|base|cuota|irpf|impuestos|retenci))(?:\s+(?:eur|euros|€))?)"
    rf"{_NO_NUM}{{0,25}}{_AMT}",
    _I,
)
_TOTAL_VAT_RE = re.compile(
    rf"^\s*total\s+(?:cuotas?\s+)?(?:de\s+)?i\.?v\.?a\.?{_NO_NUM}{{0,15}}{_AMT}", _I
)
_SKIP_AS_BASE = re.compile(r"^\s*total\s+(?:base|cuota|iva)", _I)
_VAT_WITH_RATE = re.compile(rf"\bI\.?V\.?A\.?\b[^\d\n]{{0,15}}{_PCT}{_NO_NUM}{{0,20}}{_AMT}", _I)
_VAT_AMOUNT_RE = re.compile(rf"(?:cuota\s+(?:de\s+)?)?\bI\.?V\.?A\.?\b{_NO_NUM}{{0,15}}{_AMT}", _I)
_SURCHARGE_RE = re.compile(
    r"(?:recargo(?:\s+de)?\s+equivalencia|rec\.?\s*(?:de\s+)?equiv\w*\.?|\bR\.?E\.?\b)"
    rf"{_NO_NUM}{{0,15}}(?:{_PCT}{_NO_NUM}{{0,15}})?{_AMT}",
    _I,
)
_IRPF_RE = re.compile(
    rf"\bI\.?R\.?P\.?F\.?[^\d\n]*?(?:[-\u2212]?\s*{_PCT}[^\d\n]*?)?{_AMT}",
    _I,
)

_TABLE_COLUMN = re.compile(
    r"(?P<re_amount>cuota\s+r\.?\s?e\b\.?|recargo(?:\s+de\s+equivalencia)?|rec\.?\s*equiv\w*)"
    r"|(?P<re_rate>(?:tipo|%)\s*r\.?\s?e\b\.?|%\s*rec\w*)"
    r"|(?P<rate>(?:tipo|%)\s*i\.?v\.?a\b\.?|i\.?v\.?a\s*%)"
    r"|(?P<vat>cuota(?:\s+i\.?v\.?a\b\.?)?|\bi\.?v\.?a\b)"
    r"|(?P<base>base(?:\s+imponible)?)"
    r"|(?P<total>total)",
    _I,
)
_NUMBER_TOKEN = re.compile(r"\d[\d.,]*")

# Confidence of each way of obtaining a value, before cross-validation.
_LABELLED, _TABLE, _DERIVED = 0.9, 0.85, 0.6


@dataclass
class _Labelled:
    """Amounts collected from labelled lines (Base imponible, IVA 21%, Total...)."""

    bases: list[tuple[Decimal | None, Decimal, bool]] = field(default_factory=list)
    vats: list[tuple[Decimal | None, Decimal]] = field(default_factory=list)
    surcharges: list[tuple[Decimal | None, Decimal]] = field(default_factory=list)
    totals: list[Decimal] = field(default_factory=list)
    vat_total: Decimal | None = None
    irpf: Decimal | None = None
    irpf_pct: Decimal | None = None


def _pct(text: str | None) -> Decimal | None:
    return parse_percentage(text) if text else None


class RuleBasedExtractor(Extractor):
    """Regex-based extractor; makes no network calls."""

    name = "rules"

    def extract(self, text: str, filename: str) -> Invoice:
        invoice = Invoice(filename=filename, source="rules")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self._issuer(invoice, lines)
        self._number(invoice, text)
        self._date(invoice, lines)
        self._amounts(invoice, lines)
        return invoice

    # -- issuer ----------------------------------------------------------
    def _issuer(self, f: Invoice, lines: list[str]) -> None:
        in_customer = False
        valid: list[str] = []
        labelled: list[str] = []
        issuer_lines: list[int] = []
        for i, ln in enumerate(lines):
            if _CUSTOMER_SECTION.match(ln):
                in_customer = True
            elif _ISSUER_SECTION.match(ln):
                in_customer = False
            if in_customer:
                continue
            issuer_lines.append(i)
            for hit in _TAX_ID_RE.finditer(ln):
                candidate = hit.group(1)
                if is_valid_tax_id(candidate):
                    valid.append(candidate)
                elif _TAX_ID_LABEL.search(ln):
                    labelled.append(candidate)
        if valid:
            f.tax_id, f.confidence["tax_id"] = normalize_tax_id(valid[0]), 0.95
        elif labelled:
            f.tax_id, f.confidence["tax_id"] = normalize_tax_id(labelled[0]), 0.4

        # Supplier: explicit label > line with a company suffix > first useful line.
        for i in issuer_lines:
            if m := _SUPPLIER_LABEL.match(lines[i]):
                f.supplier, f.confidence["supplier"] = self._clean_name(m.group(1)), 0.9
                return
        for i in issuer_lines:
            ln = lines[i]
            if _TITLES.match(ln) or _TAX_ID_LABEL.search(ln):
                continue
            if _COMPANY_SUFFIX.search(self._clean_name(ln)):
                f.supplier, f.confidence["supplier"] = self._clean_name(ln), 0.85
                return
        for i in issuer_lines:
            ln = lines[i]
            if _TITLES.match(ln) or re.search(r"\d", ln) or ":" in ln:
                continue
            f.supplier, f.confidence["supplier"] = self._clean_name(ln), 0.5
            return

    @staticmethod
    def _clean_name(s: str) -> str:
        s = re.sub(r"\s*[-\u2013|,]?\s*(?:N\.?I\.?F|C\.?I\.?F|DNI)\b.*$", "", s, flags=_I)
        s = re.sub(r"\s+(?:factura|invoice)$", "", s, flags=_I)  # title sharing the line (OCR)
        return s.strip(" -\u2013|,")

    # -- number and date -------------------------------------------------
    def _number(self, f: Invoice, text: str) -> None:
        if m := _NUMBER_RE.search(text):
            f.number, f.confidence["number"] = m.group(1).strip(".,;:"), 0.9

    def _date(self, f: Invoice, lines: list[str]) -> None:
        for ln in lines:
            if _DUE_DATE.search(ln):
                continue
            if (m := _DATE_LABEL.search(ln)) and (found := find_date(ln[m.end() :])):
                f.invoice_date, f.confidence["invoice_date"] = found, 0.95
                return
        for ln in lines:  # no label: first date that is not a due date
            if not re.search(r"vencim|vto", ln, _I) and (found := find_date(ln)):
                f.invoice_date, f.confidence["invoice_date"] = found, 0.6
                return

    # -- amounts ---------------------------------------------------------
    def _amounts(self, f: Invoice, lines: list[str]) -> None:
        found = self._collect(lines)
        table_lines, table_total = self._table(lines)

        total = found.totals[-1] if found.totals else table_total
        if total is not None:
            f.total = total
            f.confidence["total"] = _LABELLED if found.totals else _TABLE
        if found.irpf is not None:
            f.irpf, f.confidence["irpf"] = found.irpf, _LABELLED

        if table_lines:
            f.tax_lines = table_lines
            f.confidence["net_amount"] = f.confidence["vat_amount"] = _TABLE
        else:
            f.tax_lines = self._pair(found)
            if f.tax_lines:
                f.confidence["net_amount"] = _LABELLED
                f.confidence["vat_amount"] = _LABELLED
        self._complete(f, found)

    def _collect(self, lines: list[str]) -> _Labelled:
        found = _Labelled()
        for ln in lines:
            if (m := _TOTAL_VAT_RE.match(ln)) and (v := parse_amount(m.group(1))) is not None:
                found.vat_total = v
                continue
            if (m := _TOTAL_RE.match(ln)) and (v := parse_amount(m.group(1))) is not None:
                found.totals.append(v)
            if m := _IRPF_RE.search(ln):
                if (v := parse_amount(m.group(2))) is not None:
                    found.irpf, found.irpf_pct = abs(v), _pct(m.group(1))
                continue
            if m := _SURCHARGE_RE.search(ln):
                if (v := parse_amount(m.group(2))) is not None:
                    found.surcharges.append((_pct(m.group(1)), v))
                continue
            if (
                not _SKIP_AS_BASE.match(ln)
                and (m := _NET_RE.search(ln))
                and (v := parse_amount(m.group(3))) is not None
            ):
                is_base = m.group("label").lower().startswith("base")
                found.bases.append((_pct(m.group(2)), v, is_base))
            if (m := _VAT_WITH_RATE.search(ln)) and (v := parse_amount(m.group(2))) is not None:
                found.vats.append((_pct(m.group(1)), v))
            elif (
                not m
                and (m := _VAT_AMOUNT_RE.search(ln))
                and (v := parse_amount(m.group(1))) is not None
            ):
                found.vats.append((None, v))
        return found

    @staticmethod
    def _pair(found: _Labelled) -> list[TaxLine]:
        """Match each base with its VAT quota: by rate when stated, otherwise in order."""
        bases = found.bases
        if any(is_base for _, _, is_base in bases):  # prefer "Base" over "Subtotal"
            bases = [b for b in bases if b[2]]
        pending = list(found.vats)
        result: list[TaxLine] = []
        for rate, base, _ in bases:
            pick = next(
                (i for i, (r, _) in enumerate(pending) if rate is not None and r == rate), None
            )
            if pick is None and rate is None:
                pick = 0 if pending else None
            if pick is None:
                pick = next((i for i, (r, _) in enumerate(pending) if r is None), None)
            vat_rate, vat = pending.pop(pick) if pick is not None else (None, None)
            result.append(TaxLine(base=base, rate=rate if rate is not None else vat_rate, vat=vat))
        result.extend(TaxLine(rate=r, vat=v) for r, v in pending)
        return result

    def _table(self, lines: list[str]) -> tuple[list[TaxLine], Decimal | None]:
        """Layout with a header row ('Base imponible | % IVA | Cuota IVA') and a row per rate."""
        for i, header in enumerate(lines[:-1]):
            if re.search(r"\d", header) or "base" not in header.lower():
                continue
            columns = [m.lastgroup for m in _TABLE_COLUMN.finditer(header)]
            if len(columns) < 3 or "base" not in columns or "vat" not in columns:
                continue
            rows: list[TaxLine] = []
            row_totals: list[Decimal] = []
            for row in lines[i + 1 :]:
                tokens = _NUMBER_TOKEN.findall(row)
                if len(tokens) != len(columns):
                    break
                cells = dict(zip(columns, tokens, strict=True))
                line = TaxLine(
                    base=parse_amount(cells["base"]),
                    rate=parse_percentage(cells["rate"]) if "rate" in cells else None,
                    vat=parse_amount(cells["vat"]),
                    surcharge_rate=parse_percentage(cells["re_rate"])
                    if "re_rate" in cells
                    else None,
                    surcharge=parse_amount(cells["re_amount"]) if "re_amount" in cells else None,
                )
                rows.append(line)
                if "total" in cells and (value := parse_amount(cells["total"])) is not None:
                    row_totals.append(value)
            if rows:
                return rows, sum(row_totals, Decimal(0)) if row_totals else None
        return [], None

    def _complete(self, f: Invoice, found: _Labelled) -> None:
        """Fill in what can be deduced from what was read, at reduced confidence."""
        derived = False
        for line in f.tax_lines:
            if line.base is not None and line.vat is not None and line.rate is None:
                line.rate, derived = infer_rate(line.base, line.vat), True
            elif line.base is not None and line.rate is not None and line.vat is None:
                line.vat, derived = (line.base * line.rate / 100).quantize(Decimal("0.01")), True
        if len(f.tax_lines) == 1 and f.tax_lines[0].vat is None and found.vat_total is not None:
            f.tax_lines[0].vat, derived = found.vat_total, True

        self._attach_surcharges(f, found)

        if (
            len(f.tax_lines) == 1
            and f.tax_lines[0].base is not None
            and f.tax_lines[0].vat is None
            and f.total is not None
            and abs(f.total - f.tax_lines[0].base + (f.irpf or 0)) <= Decimal("0.02")
        ):  # VAT-exempt invoice
            f.tax_lines[0].vat, f.tax_lines[0].rate = Decimal("0.00"), Decimal(0)
            derived = True
        if derived and f.tax_lines:
            f.confidence["vat_amount"] = _DERIVED
        if found.irpf_pct is not None and f.irpf is None and f.net_amount is not None:
            f.irpf = (f.net_amount * found.irpf_pct / 100).quantize(Decimal("0.01"))
            f.confidence["irpf"] = 0.7
        for line in f.tax_lines:
            if line.rate is not None:
                line.rate = normalize_rate(line.rate)

    @staticmethod
    def _attach_surcharges(f: Invoice, found: _Labelled) -> None:
        for re_rate, amount in found.surcharges:
            target = next(
                (
                    line
                    for line in f.tax_lines
                    if line.surcharge is None
                    and (
                        re_rate is None
                        or (line.rate is not None and SURCHARGE_FOR_RATE.get(line.rate) == re_rate)
                    )
                ),
                None,
            ) or next((line for line in f.tax_lines if line.surcharge is None), None)
            if target is not None:
                target.surcharge, target.surcharge_rate = amount, re_rate

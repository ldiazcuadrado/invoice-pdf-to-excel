"""Domain models shared by extraction, validation and reporting."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, computed_field

REQUIRED_FIELDS: tuple[str, ...] = (
    "supplier",
    "tax_id",
    "number",
    "invoice_date",
    "net_amount",
    "total",
)

Source = Literal["rules", "llm"]


class TaxLine(BaseModel):
    """One VAT bracket of an invoice: a taxable base, its rate and the resulting quota.

    `surcharge_*` carry the recargo de equivalencia that applies on top of the VAT
    for retailers under that special regime.
    """

    base: Decimal | None = None
    rate: Decimal | None = None
    vat: Decimal | None = None
    surcharge_rate: Decimal | None = None
    surcharge: Decimal | None = None


class Invoice(BaseModel):
    filename: str
    supplier: str | None = None
    tax_id: str | None = None
    number: str | None = None
    invoice_date: date | None = None
    tax_lines: list[TaxLine] = Field(default_factory=list)
    irpf: Decimal | None = None  # withheld amount, positive
    total: Decimal | None = None
    problems: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    source: Source = "rules"
    ocr: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def net_amount(self) -> Decimal | None:
        bases = [line.base for line in self.tax_lines if line.base is not None]
        return sum(bases, Decimal(0)) if bases else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def vat_amount(self) -> Decimal | None:
        quotas = [line.vat for line in self.tax_lines if line.vat is not None]
        return sum(quotas, Decimal(0)) if quotas else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def surcharge_amount(self) -> Decimal | None:
        amounts = [line.surcharge for line in self.tax_lines if line.surcharge is not None]
        return sum(amounts, Decimal(0)) if amounts else None

    @property
    def gross_amount(self) -> Decimal | None:
        """Operation value including VAT and surcharge, before any IRPF withholding."""
        if self.net_amount is not None:
            return self.net_amount + (self.vat_amount or Decimal(0)) + (self.surcharge_amount or 0)
        if self.total is not None:
            return self.total + (self.irpf or Decimal(0))
        return None

    @property
    def missing_fields(self) -> list[str]:
        values = {
            "supplier": self.supplier,
            "tax_id": self.tax_id,
            "number": self.number,
            "invoice_date": self.invoice_date,
            "net_amount": self.net_amount,
            "total": self.total,
        }
        return [name for name in REQUIRED_FIELDS if values[name] is None]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def overall_confidence(self) -> float:
        """Mean confidence of the required fields, discounted when validation found problems."""
        score = sum(self.confidence.get(name, 0.0) for name in REQUIRED_FIELDS)
        score /= len(REQUIRED_FIELDS)
        return round(score * (0.8 if self.problems else 1.0), 3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_reasons(self) -> list[str]:
        reasons = list(self.problems)
        if self.duplicate_of:
            reasons.append(f"Possible duplicate invoice (same as {self.duplicate_of})")
        return reasons

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_ok(self) -> bool:
        return not self.problems and self.duplicate_of is None

    def low_confidence_fields(self, threshold: float) -> list[str]:
        return [name for name in REQUIRED_FIELDS if self.confidence.get(name, 0.0) < threshold]


class FileIssue(BaseModel):
    filename: str
    reason: str


class Result(BaseModel):
    invoices: list[Invoice] = Field(default_factory=list)
    file_issues: list[FileIssue] = Field(default_factory=list)

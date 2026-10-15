"""Generate the test PDF invoices (ENTIRELY FICTITIOUS) in test_data/invoices/.

Usage:  uv run python scripts/generate_test_data.py [target_folder]

The invoices are written in Spanish format on purpose (Spanish labels, NIF/CIF, IVA,
IRPF, recargo de equivalencia, dd/mm/yyyy dates, decimal comma), because parsing
Spanish invoices is what the tool does. Companies, people and tax IDs are invented;
the tax IDs only have valid check characters so that they pass validation.

Templates A-D have a single VAT rate and differ in labels and date formats. E lists
one base and quota per rate, F is a table with equivalence surcharge columns. The
"scanned" template renders template A to an image and embeds only that image, so the
PDF has no text layer and needs OCR. Two files are deliberate edge cases: a wrong
total and a duplicate.

Needs the dev dependencies and the `ocr` extra (`uv sync --all-extras`).
"""

from __future__ import annotations

import io
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple

import pypdfium2
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from invoice_to_excel.normalizers import SURCHARGE_FOR_RATE
from invoice_to_excel.validation import cif_check, dni_letter

W, H = A4
SCAN_DPI = 300
MONTHS_ES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


def cif(letter: str, digits: str) -> str:
    digit, control_letter = cif_check(digits)
    return f"{letter}{digits}{control_letter if letter in 'KPQRSNW' else digit}"


def dni(num: int) -> str:
    return f"{num:08d}{dni_letter(num)}"


def eur(x: Decimal) -> str:
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def q(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), ROUND_HALF_UP)


def pct(x: Decimal) -> str:
    return f"{x:f}".replace(".", ",")


@dataclass
class Party:
    name: str
    tax_id: str
    address: str


class Line(NamedTuple):
    description: str
    quantity: int
    unit_price: str
    vat: int | None = None  # overrides Spec.vat for multi-rate invoices


@dataclass
class Spec:
    filename: str
    template: str  # A | B | C | D | E | F | scanned
    issuer: Party
    number: str
    invoice_date: date
    lines: list[Line]
    vat: int = 21
    irpf: int = 0
    surcharge: bool = False  # recargo de equivalencia
    total_adjustment: str = "0"  # deliberate error to exercise validation


@dataclass
class TaxGroup:
    rate: int
    base: Decimal
    vat: Decimal
    surcharge_rate: Decimal | None
    surcharge: Decimal


@dataclass
class Amounts:
    groups: list[TaxGroup]
    irpf: Decimal
    total: Decimal

    @property
    def net(self) -> Decimal:
        return sum((g.base for g in self.groups), Decimal(0))

    @property
    def vat(self) -> Decimal:
        return sum((g.vat for g in self.groups), Decimal(0))


CUSTOMER = Party("Cliente Ficticio, S.L.", cif("B", "1234567"), "Calle de Prueba 1, 28000 Madrid")

E_SUMI = Party(
    "Suministros Ficticios, S.L.", cif("B", "8765432"), "Calle Inventada 12, 46001 Valencia"
)
E_PAPE = Party(
    "Papelería Imaginaria, S.A.", cif("A", "2468135"), "Avenida de la Muestra 45, 08001 Barcelona"
)
E_MARTA = Party("Marta Ficticia Ruiz", dni(12345678), "Plaza Falsa 3, 41001 Sevilla")
E_TALL = Party(
    "Talleres Inventados, S.L.", cif("B", "1357924"), "Polígono Ficticio, nave 7, 50001 Zaragoza"
)
E_CONS = Party(
    "Consultoría Falsa, S.L.P.", cif("B", "9988776"), "Paseo de la Muestra 8, 48001 Bilbao"
)
E_SCAN = Party("Reformas Escaneadas, S.L.", cif("B", "5544332"), "Camino de Prueba 2, 29001 Málaga")
E_FERR = Party(
    "Ferretería Ficticia, S.L.", cif("B", "2233445"), "Calle del Ejemplo 20, 15001 A Coruña"
)
E_DIST = Party(
    "Distribuciones Imaginarias, S.A.", cif("A", "3141592"), "Calle Mayorista 9, 47001 Valladolid"
)

SUPPLIES_014 = Spec(
    "supplies_2026-014.pdf",
    "A",
    E_SUMI,
    "2026-014",
    date(2026, 2, 14),
    [Line("Mobiliario de almacén", 2, "245.00"), Line("Montaje e instalación", 1, "80.00")],
)

SPECS = [
    Spec(
        "supplies_2026-001.pdf",
        "A",
        E_SUMI,
        "2026-001",
        date(2026, 1, 15),
        [Line("Material de oficina variado", 10, "12.50"), Line("Cartuchos de tinta", 4, "31.90")],
    ),
    SUPPLIES_014,
    Spec(
        "supplies_2026-014_copy.pdf", "A", E_SUMI, "2026-014", date(2026, 2, 14), SUPPLIES_014.lines
    ),  # same invoice received twice
    Spec(
        "supplies_2026-027.pdf",
        "A",
        E_SUMI,
        "2026-027",
        date(2026, 3, 30),
        [Line("Papel reciclado A4 (cajas)", 25, "18.40")],
    ),
    Spec(
        "stationery_F0031.pdf",
        "C",
        E_PAPE,
        "2026/0031",
        date(2026, 4, 3),
        [Line("Libros de texto", 12, "27.35"), Line("Cuadernos", 40, "1.80")],
        vat=4,
    ),
    Spec(
        "stationery_F0058.pdf",
        "C",
        E_PAPE,
        "2026/0058",
        date(2026, 5, 20),
        [Line("Material escolar surtido", 1, "1250.00"), Line("Envío urgente", 1, "35.90")],
        vat=10,
    ),
    Spec(
        "stationery_F0077_multi.pdf",
        "E",
        E_PAPE,
        "2026/0077",
        date(2026, 7, 9),
        [
            Line("Libros de texto", 30, "24.90", 4),
            Line("Material de dibujo", 15, "13.40", 21),
            Line("Cuadernos y carpetas", 60, "2.35", 21),
            Line("Servicio de encuadernación", 1, "180.00", 10),
        ],
    ),
    Spec(
        "marta_design_03.pdf",
        "B",
        E_MARTA,
        "F-2026/003",
        date(2026, 3, 15),
        [
            Line("Diseño de identidad visual", 1, "1200.00"),
            Line("Adaptaciones para redes", 3, "150.00"),
        ],
        irpf=15,
    ),
    Spec(
        "marta_design_07.pdf",
        "B",
        E_MARTA,
        "F-2026/007",
        date(2026, 6, 5),
        [Line("Maquetación de catálogo", 1, "2300.00")],
        irpf=15,
    ),
    Spec(
        "workshop_A-1042.pdf",
        "D",
        E_TALL,
        "A-1042",
        date(2026, 4, 28),
        [Line("Mecanizado de piezas", 120, "6.75"), Line("Tratamiento superficial", 1, "340.00")],
    ),
    Spec(
        "workshop_A-1077.pdf",
        "D",
        E_TALL,
        "A-1077",
        date(2026, 6, 12),
        [Line("Reparación de maquinaria", 1, "1860.50")],
    ),
    Spec(
        "consulting_2026-12.pdf",
        "B",
        E_CONS,
        "C-2026/012",
        date(2026, 5, 8),
        [Line("Asesoramiento estratégico (horas)", 18, "95.00")],
        irpf=7,
    ),
    Spec(
        "hardware_F-2026-0118.pdf",
        "E",
        E_FERR,
        "F-2026-0118",
        date(2026, 7, 21),
        [
            Line("Herramientas manuales", 8, "14.75", 21),
            Line("Material eléctrico", 25, "6.40", 21),
            Line("Instalación en vivienda", 1, "420.00", 10),
        ],
    ),
    Spec(
        "hardware_F-2026-0204.pdf",
        "E",
        E_FERR,
        "F-2026-0204",
        date(2026, 10, 5),
        [
            Line("Tornillería surtida", 40, "3.15", 21),
            Line("Obra menor en vivienda", 1, "980.00", 10),
        ],
    ),
    Spec(
        "wholesale_B-5521.pdf",
        "F",
        E_DIST,
        "B-5521",
        date(2026, 8, 17),
        [
            Line("Conservas variadas (cajas)", 12, "36.00", 10),
            Line("Bebidas sin alcohol (cajas)", 20, "14.50", 21),
        ],
        surcharge=True,
    ),
    Spec(
        "wholesale_B-5598.pdf",
        "F",
        E_DIST,
        "B-5598",
        date(2026, 9, 18),
        [Line("Productos de limpieza (cajas)", 15, "22.80", 21)],
        surcharge=True,
    ),
    # Deliberately wrong total: the invoice states 10 EUR more than its parts add up to.
    Spec(
        "supplies_2026-031.pdf",
        "A",
        E_SUMI,
        "2026-031",
        date(2026, 4, 9),
        [Line("Estanterías metálicas", 3, "132.00")],
        total_adjustment="10.00",
    ),
    Spec(
        "renovations_scanned.pdf",
        "scanned",
        E_SCAN,
        "R-2026-05",
        date(2026, 3, 2),
        [Line("Reforma de baño", 1, "3200.00"), Line("Retirada de escombros", 1, "240.00")],
    ),
]


def amounts(s: Spec) -> Amounts:
    bases: dict[int, Decimal] = {}
    for line in s.lines:
        rate = line.vat if line.vat is not None else s.vat
        bases[rate] = bases.get(rate, Decimal(0)) + Decimal(line.quantity) * Decimal(
            line.unit_price
        )
    groups = []
    for rate in sorted(bases, reverse=True):
        base = q(bases[rate])
        re_rate = SURCHARGE_FOR_RATE.get(Decimal(rate)) if s.surcharge else None
        groups.append(
            TaxGroup(
                rate,
                base,
                q(base * rate / 100),
                re_rate,
                q(base * re_rate / 100) if re_rate is not None else Decimal(0),
            )
        )
    net = sum((g.base for g in groups), Decimal(0))
    irpf = q(net * s.irpf / 100)
    total = sum((g.base + g.vat + g.surcharge for g in groups), Decimal(0)) - irpf
    return Amounts(groups, irpf, total + Decimal(s.total_adjustment))


def date_text(s: Spec) -> str:
    d = s.invoice_date
    return {
        "A": d.strftime("%d/%m/%Y"),
        "B": f"{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}",
        "C": d.strftime("%d-%m-%Y"),
        "D": d.strftime("%d.%m.%Y"),
        "E": d.strftime("%d/%m/%Y"),
        "F": d.strftime("%d/%m/%Y"),
        "scanned": d.strftime("%d/%m/%Y"),
    }[s.template]


# ------------------------------------------------------------ templates


def _line_table(
    c: canvas.Canvas, s: Spec, y: float, x_start: int = 50, x_end: int = 545, show_vat: bool = False
) -> float:
    qty_x, price_x, vat_x = (
        (x_end - 250, x_end - 165, x_end - 85) if show_vat else (x_end - 190, x_end - 100, 0)
    )
    c.setFont("Helvetica-Bold", 9)
    c.drawString(x_start, y, "Concepto")
    c.drawRightString(qty_x, y, "Cant.")
    c.drawRightString(price_x, y, "Precio")
    if show_vat:
        c.drawRightString(vat_x, y, "% IVA")
    c.drawRightString(x_end, y, "Importe")
    c.line(x_start, y - 4, x_end, y - 4)
    c.setFont("Helvetica", 9)
    y -= 20
    for line in s.lines:
        c.drawString(x_start, y, line.description)
        c.drawRightString(qty_x, y, str(line.quantity))
        c.drawRightString(price_x, y, eur(Decimal(line.unit_price)))
        if show_vat:
            c.drawRightString(vat_x, y, f"{line.vat if line.vat is not None else s.vat} %")
        c.drawRightString(x_end, y, eur(q(Decimal(line.quantity) * Decimal(line.unit_price))))
        y -= 16
    return y - 10


def _pairs(
    c: canvas.Canvas, y: float, rows: list[tuple[str, str]], x_label: int = 330, x_val: int = 545
) -> float:
    for i, (label, val) in enumerate(rows):
        c.setFont("Helvetica-Bold" if i == len(rows) - 1 else "Helvetica", 10)
        c.drawString(x_label, y, label)
        c.drawRightString(x_val, y, val)
        y -= 16
    return y


def _header_block_a(c: canvas.Canvas, s: Spec) -> None:
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, H - 60, s.issuer.name)
    c.setFont("Helvetica", 9)
    c.drawString(50, H - 76, s.issuer.address)
    c.drawString(50, H - 88, f"NIF: {s.issuer.tax_id}")
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(545, H - 60, "FACTURA")
    c.setFont("Helvetica", 10)
    c.drawRightString(545, H - 80, f"Nº Factura: {s.number}")
    c.drawRightString(545, H - 94, f"Fecha: {date_text(s)}")
    due = s.invoice_date.replace(day=28).strftime("%d/%m/%Y")
    c.drawRightString(545, H - 108, f"Vencimiento: {due}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, H - 140, "Cliente:")
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 154, CUSTOMER.name)
    c.drawString(50, H - 168, CUSTOMER.address)
    c.drawString(50, H - 182, f"NIF: {CUSTOMER.tax_id}")


def template_a(c: canvas.Canvas, s: Spec) -> None:
    t = amounts(s)
    _header_block_a(c, s)
    y = _line_table(c, s, H - 230)
    rows = [
        ("Base imponible", eur(t.net)),
        (f"IVA {s.vat}%", eur(t.vat)),
        ("Total factura", eur(t.total)),
    ]
    _pairs(c, y - 10, rows)


def template_b(c: canvas.Canvas, s: Spec) -> None:
    t = amounts(s)
    c.setFillColorRGB(0.12, 0.31, 0.47)
    c.rect(0, H - 90, W, 90, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, H - 45, "Factura")
    c.setFont("Helvetica", 11)
    c.drawString(40, H - 65, f"Factura nº {s.number}")
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(40, H - 120, s.issuer.name)
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 134, s.issuer.address)
    c.drawString(40, H - 146, f"CIF/NIF: {s.issuer.tax_id}")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 170, f"Fecha de emisión: {date_text(s)}")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, H - 200, "Facturar a")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 214, CUSTOMER.name)
    c.drawString(40, H - 228, f"CIF: {CUSTOMER.tax_id}")
    y = _line_table(c, s, H - 270, 40, 555)
    rows = [("Subtotal", eur(t.net)), (f"IVA ({s.vat}%)", eur(t.vat))]
    if s.irpf:
        rows.append((f"IRPF (-{s.irpf}%)", f"-{eur(t.irpf)}"))
    rows.append(("TOTAL", eur(t.total)))
    _pairs(c, y - 10, rows, 340, 555)


def template_c(c: canvas.Canvas, s: Spec) -> None:
    t = amounts(s)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, H - 60, s.issuer.name)
    c.setFont("Helvetica", 9)
    c.drawString(60, H - 74, f"{s.issuer.address}  -  CIF {s.issuer.tax_id}")
    c.setFont("Helvetica", 11)
    c.drawString(60, H - 110, f"Fra. nº {s.number}")
    c.drawString(60, H - 126, f"Fecha: {date_text(s)}")
    c.setFont("Helvetica", 10)
    c.drawString(60, H - 160, "Destinatario:")
    c.drawString(60, H - 174, f"{CUSTOMER.name} - {CUSTOMER.tax_id}")
    y = _line_table(c, s, H - 215, 60, 535)
    rows = [("Base", eur(t.net)), (f"IVA {s.vat}%", eur(t.vat)), ("Total a pagar", eur(t.total))]
    _pairs(c, y - 10, rows, 320, 535)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(60, 60, "Forma de pago: transferencia bancaria. Documento de prueba ficticio.")


def template_d(c: canvas.Canvas, s: Spec) -> None:
    t = amounts(s)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(50, H - 60, s.issuer.name)
    c.setFont("Helvetica", 9)
    c.drawString(50, H - 74, s.issuer.address)
    c.drawString(50, H - 86, f"C.I.F.: {s.issuer.tax_id}")
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 120, f"Número de factura: {s.number}")
    c.drawString(50, H - 134, f"Fecha factura: {date_text(s)}")
    c.drawString(50, H - 160, "Datos del cliente")
    c.drawString(50, H - 174, f"{CUSTOMER.name}   NIF {CUSTOMER.tax_id}")
    y = _line_table(c, s, H - 215)
    c.setFont("Helvetica-Bold", 9)
    xs = [170, 270, 390, 520]
    for x, label in zip(xs, ["Base imponible", "Tipo IVA", "Cuota IVA", "Total"], strict=True):
        c.drawRightString(x, y - 10, label)
    c.setFont("Helvetica", 10)
    for x, value in zip(xs, [eur(t.net), f"{s.vat} %", eur(t.vat), eur(t.total)], strict=True):
        c.drawRightString(x, y - 28, value)
    c.rect(50, y - 36, 495, 36)


def template_e(c: canvas.Canvas, s: Spec) -> None:
    """One base and one quota per VAT rate, then the total."""
    t = amounts(s)
    _header_block_a(c, s)
    y = _line_table(c, s, H - 230, show_vat=True)
    rows: list[tuple[str, str]] = []
    for g in t.groups:
        rows += [(f"Base imponible {g.rate}%", eur(g.base)), (f"IVA {g.rate}%", eur(g.vat))]
    rows.append(("Total factura", eur(t.total)))
    _pairs(c, y - 10, rows)


def template_f(c: canvas.Canvas, s: Spec) -> None:
    """Breakdown table with recargo de equivalencia columns."""
    t = amounts(s)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, H - 60, s.issuer.name)
    c.setFont("Helvetica", 9)
    c.drawString(50, H - 74, s.issuer.address)
    c.drawString(50, H - 86, f"NIF: {s.issuer.tax_id}")
    c.setFont("Helvetica", 10)
    c.drawString(50, H - 120, f"Factura nº {s.number}")
    c.drawString(50, H - 134, f"Fecha: {date_text(s)}")
    c.drawString(50, H - 160, "Cliente:")
    c.drawString(50, H - 174, f"{CUSTOMER.name}   NIF {CUSTOMER.tax_id}")
    c.drawString(50, H - 188, "Régimen especial del recargo de equivalencia")
    y = _line_table(c, s, H - 230, show_vat=True)
    xs = [140, 195, 275, 335, 420]
    c.setFont("Helvetica-Bold", 9)
    for x, label in zip(
        xs, ["Base imponible", "% IVA", "Cuota IVA", "% R.E.", "Cuota R.E."], strict=True
    ):
        c.drawRightString(x, y - 10, label)
    c.setFont("Helvetica", 10)
    row_y = y - 28
    for g in t.groups:
        values = [
            eur(g.base),
            f"{g.rate} %",
            eur(g.vat),
            f"{pct(g.surcharge_rate or Decimal(0))} %",
            eur(g.surcharge),
        ]
        for x, value in zip(xs, values, strict=True):
            c.drawRightString(x, row_y, value)
        row_y -= 16
    _pairs(c, row_y - 14, [("Total factura", eur(t.total))])


def template_scanned(c: canvas.Canvas, s: Spec) -> None:
    """Page holding only an image of template A: there is no text layer."""
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4, invariant=1)
    template_a(page, s)
    page.showPage()
    page.save()
    pdf = pypdfium2.PdfDocument(buffer.getvalue())
    try:
        image = pdf[0].render(scale=SCAN_DPI / 72).to_pil().convert("L")
    finally:
        pdf.close()
    c.drawImage(ImageReader(image), 0, 0, width=W, height=H)


TEMPLATES: dict[str, Callable[[canvas.Canvas, Spec], None]] = {
    "A": template_a,
    "B": template_b,
    "C": template_c,
    "D": template_d,
    "E": template_e,
    "F": template_f,
    "scanned": template_scanned,
}


def generate(target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    output = []
    for s in SPECS:
        path = target / s.filename
        c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
        c.setTitle(f"Test invoice {s.number}")
        TEMPLATES[s.template](c, s)
        c.showPage()
        c.save()
        output.append(path)
    return output


if __name__ == "__main__":
    default = Path(__file__).resolve().parents[1] / "test_data" / "invoices"
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    for p in generate(out):
        print("generated", p)

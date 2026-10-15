"""Web UI and JSON API.

The server is stateless: uploads live in a temporary directory for the duration of
one request, and exports are rebuilt from the results the browser sends back.
Nothing is written to disk by default.
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request

from invoice_to_excel import __version__
from invoice_to_excel.config import Settings
from invoice_to_excel.extraction import Pipeline, build_pipeline
from invoice_to_excel.models import FileIssue, Result
from invoice_to_excel.reporting import (
    SupplierYearRow,
    VatQuarterRow,
    quarterly_vat_summary,
    render_csv,
    render_excel,
    supplier_annual_totals,
)
from invoice_to_excel.validation import flag_duplicates

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
PDF_MAGIC = b"%PDF-"
CHUNK = 1024 * 1024
MAX_REPORT_INVOICES = 5000

_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class Report(Result):
    """A result plus the accountant summaries derived from it."""

    vat_quarterly: list[VatQuarterRow] = Field(default_factory=list)
    suppliers_347: list[SupplierYearRow] = Field(default_factory=list)
    confidence_threshold: float = 0.7


def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            413, f"{upload.filename or 'file'} exceeds the {limit // CHUNK} MB limit"
        )
    return data


def _safe_name(raw: str | None, index: int) -> str:
    name = Path((raw or "").replace("\\", "/")).name.replace("\x00", "")
    name = name or f"upload-{index}.pdf"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    settings = settings or Settings()
    pipeline = pipeline or build_pipeline(settings)
    max_bytes = settings.web_max_upload_mb * CHUNK
    pdf_lock = threading.Lock()  # pdfium and Tesseract wrappers are not safe to run concurrently

    app = FastAPI(title="invoice-pdf-to-excel", version=__version__, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return {
            "version": __version__,
            "max_upload_mb": settings.web_max_upload_mb,
            "max_files": settings.web_max_files,
            "confidence_threshold": settings.confidence_threshold,
            "extractor": pipeline.mode,
            "ocr": settings.ocr_enabled,
            "sends_data_to_third_party": pipeline.mode != "rules",
        }

    @app.post("/api/process")
    def process(files: Annotated[list[UploadFile], File()]) -> Result:
        if len(files) > settings.web_max_files:
            raise HTTPException(413, f"At most {settings.web_max_files} files per request")
        result = Result()
        with tempfile.TemporaryDirectory(prefix="invoice-upload-") as tmp:
            for index, upload in enumerate(files):
                name = _safe_name(upload.filename, index)
                data = _read_limited(upload, max_bytes)
                if not data.startswith(PDF_MAGIC):
                    result.file_issues.append(FileIssue(filename=name, reason="Not a PDF file"))
                    continue
                folder = Path(tmp) / str(index)
                folder.mkdir()
                path = folder / name
                path.write_bytes(data)
                with pdf_lock:
                    outcome = pipeline.process_pdf(path)
                if isinstance(outcome, FileIssue):
                    result.file_issues.append(outcome)
                else:
                    result.invoices.append(outcome)
        flag_duplicates(result.invoices)
        return result

    def _checked(result: Result) -> Result:
        if len(result.invoices) > MAX_REPORT_INVOICES:
            raise HTTPException(413, f"At most {MAX_REPORT_INVOICES} invoices per report")
        flag_duplicates(result.invoices)
        return result

    @app.post("/api/report")
    def report(result: Result) -> Report:
        result = _checked(result)
        return Report(
            invoices=result.invoices,
            file_issues=result.file_issues,
            vat_quarterly=quarterly_vat_summary(result.invoices),
            suppliers_347=supplier_annual_totals(result.invoices),
            confidence_threshold=settings.confidence_threshold,
        )

    @app.post("/api/export/{fmt}")
    def export(fmt: Literal["xlsx", "csv"], result: Result) -> Response:
        result = _checked(result)
        if fmt == "xlsx":
            body = render_excel(result, settings.confidence_threshold)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            body = ("﻿" + render_csv(result, settings.csv_locale)).encode("utf-8")
            media = "text/csv; charset=utf-8"
        return Response(
            body,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="invoices_report.{fmt}"'},
        )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def run(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=host or settings.web_host,
        port=port or settings.web_port,
        log_config=None,
    )

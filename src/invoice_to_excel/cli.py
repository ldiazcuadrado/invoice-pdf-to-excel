"""Command line interface."""

from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
from typing import Annotated

import typer

from invoice_to_excel import __version__
from invoice_to_excel.config import ExtractorMode, Settings
from invoice_to_excel.extraction import ConfigurationError, Pipeline, build_pipeline
from invoice_to_excel.logging_config import configure_logging
from invoice_to_excel.reporting import write_csv, write_excel
from invoice_to_excel.watch import FolderWatcher, RunningReport

log = logging.getLogger(__name__)

app = typer.Typer(
    name="invoice-to-excel",
    help="Convert Spanish PDF invoices into validated Excel reports.",
    no_args_is_help=True,
    add_completion=False,
)

ExtractorOption = Annotated[
    ExtractorMode | None,
    typer.Option(
        "--extractor",
        help="rules (default), llm, or auto (rules, then the LLM when incomplete). "
        "llm and auto send invoice text to the Anthropic API and need INVOICE_LLM_ENABLED=true.",
    ),
]
OutOption = Annotated[Path, typer.Option("--out", help="Output Excel path")]
CsvLocaleOption = Annotated[
    str | None,
    typer.Option(
        "--csv-locale", help="'en': comma separator, dot decimal. 'es': semicolon, decimal comma."
    ),
]
NoCsvOption = Annotated[
    bool, typer.Option("--no-csv", help="Do not write the CSV next to the Excel file")
]
OcrOption = Annotated[
    bool | None, typer.Option("--ocr/--no-ocr", help="OCR for scanned PDFs (default: on)")
]


def _version(value: bool) -> None:
    if value:
        typer.echo(f"invoice-to-excel {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
    log_format: Annotated[
        str | None, typer.Option(help="Log format: 'text' or 'json' (default: text)")
    ] = None,
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version")
    ] = False,
) -> None:
    settings = Settings()
    updates: dict[str, object] = {}
    if verbose:
        updates["log_level"] = "DEBUG"
    if log_format:
        updates["log_format"] = log_format
    if log_format and log_format not in ("text", "json"):
        raise typer.BadParameter("must be 'text' or 'json'", param_hint="--log-format")
    if updates:
        settings = settings.model_copy(update=updates)
    configure_logging(settings.log_level, settings.log_format)
    ctx.obj = settings


def _pipeline(
    settings: Settings, extractor: ExtractorMode | None, ocr: bool | None
) -> tuple[Settings, Pipeline]:
    if ocr is not None:
        settings = settings.model_copy(update={"ocr_enabled": ocr})
    try:
        return settings, build_pipeline(settings, extractor)
    except ConfigurationError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc


def _locale(settings: Settings, value: str | None) -> str:
    chosen = value or settings.csv_locale
    if chosen not in ("en", "es"):
        typer.secho("error: --csv-locale must be 'en' or 'es'", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    return chosen


@app.command()
def process(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(help="Folder containing the PDFs")],
    out: OutOption = Path("reports/invoices_report.xlsx"),
    recursive: Annotated[bool, typer.Option("--recursive", help="Include subfolders")] = False,
    no_csv: NoCsvOption = False,
    csv_locale: CsvLocaleOption = None,
    extractor: ExtractorOption = None,
    ocr: OcrOption = None,
) -> None:
    """Process a folder of PDF invoices into an Excel report (and CSV)."""
    settings, pipeline = _pipeline(ctx.obj, extractor, ocr)
    locale = _locale(settings, csv_locale)
    if not folder.is_dir():
        typer.secho(f"error: folder does not exist: {folder}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    result = pipeline.process_folder(folder, recursive)
    if not result.invoices and not result.file_issues:
        typer.secho(f"No PDF found in {folder}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)

    xlsx = write_excel(result, out, settings.confidence_threshold)
    typer.echo(f"Excel: {xlsx}")
    if not no_csv:
        typer.echo(f"CSV:   {write_csv(result, out.with_suffix('.csv'), locale)}")
    to_review = sum(1 for i in result.invoices if not i.is_ok)
    typer.echo(
        f"Summary: {len(result.invoices)} invoices read ({to_review} to review), "
        f"{len(result.file_issues)} files with issues"
    )


@app.command()
def watch(
    ctx: typer.Context,
    folder: Annotated[Path, typer.Argument(help="Folder to watch for new PDFs")],
    out: OutOption = Path("reports/invoices_report.xlsx"),
    no_csv: NoCsvOption = False,
    csv_locale: CsvLocaleOption = None,
    extractor: ExtractorOption = None,
    ocr: OcrOption = None,
    existing: Annotated[
        bool,
        typer.Option("--existing/--no-existing", help="Also process PDFs already in the folder"),
    ] = True,
) -> None:
    """Process PDFs as they appear, appending to a running report.

    Processed files move to FOLDER/processed/, unreadable ones to FOLDER/failed/.
    """
    settings, pipeline = _pipeline(ctx.obj, extractor, ocr)
    if not folder.is_dir():
        typer.secho(f"error: folder does not exist: {folder}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    report = RunningReport(
        out,
        csv_locale=_locale(settings, csv_locale),
        write_csv_file=not no_csv,
        confidence_threshold=settings.confidence_threshold,
    )
    watcher = FolderWatcher(folder, pipeline, report, settle_seconds=settings.watch_settle_seconds)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        watcher.run(stop, process_existing=existing)
    except KeyboardInterrupt:
        stop.set()
    typer.echo(f"Report: {out}")


@app.command()
def serve(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1)")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default 8000)")] = None,
) -> None:
    """Run the web interface. It has no authentication: keep it on trusted networks."""
    try:
        from invoice_to_excel.web import run
    except ImportError as exc:
        typer.secho(
            "error: the web interface needs the 'web' extra: "
            "pip install 'invoice-pdf-to-excel[web]'",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2) from exc
    settings: Settings = ctx.obj
    try:
        run(settings, host, port)
    except ConfigurationError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

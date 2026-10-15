"""Watch-folder mode: process PDFs as they appear and keep a running report."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from invoice_to_excel.extraction import Pipeline, list_pdfs
from invoice_to_excel.models import FileIssue, Invoice, Result
from invoice_to_excel.reporting import write_csv, write_excel
from invoice_to_excel.validation import flag_duplicates

log = logging.getLogger(__name__)

PROCESSED_DIR = "processed"
FAILED_DIR = "failed"
TICK_SECONDS = 0.2


class RunningReport:
    """An Excel/CSV report that grows as invoices arrive.

    Every result is appended to a JSON-lines ledger next to the workbook, so the
    report survives restarts and is rebuilt in full (with duplicate detection across
    all runs) each time something is added.
    """

    def __init__(
        self,
        xlsx: Path,
        *,
        csv_locale: str = "en",
        write_csv_file: bool = True,
        confidence_threshold: float = 0.7,
    ):
        self.xlsx = xlsx
        self.ledger = xlsx.with_suffix(".jsonl")
        self._csv_locale = csv_locale
        self._write_csv = write_csv_file
        self._threshold = confidence_threshold
        self.result = Result()
        self._load()

    def _load(self) -> None:
        if not self.ledger.exists():
            return
        for line in self.ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            kind, _, payload = line.partition("\t")
            if kind == "invoice":
                self.result.invoices.append(Invoice.model_validate_json(payload))
            elif kind == "issue":
                self.result.file_issues.append(FileIssue.model_validate_json(payload))
        log.info(
            "loaded running report",
            extra={"invoices": len(self.result.invoices), "issues": len(self.result.file_issues)},
        )

    def add(self, outcome: Invoice | FileIssue) -> None:
        self.xlsx.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as fh:
            if isinstance(outcome, FileIssue):
                self.result.file_issues.append(outcome)
                fh.write(f"issue\t{outcome.model_dump_json()}\n")
            else:
                self.result.invoices.append(outcome)
                fh.write(f"invoice\t{outcome.model_dump_json()}\n")
        self.write()

    def write(self) -> None:
        flag_duplicates(self.result.invoices)
        write_excel(self.result, self.xlsx, self._threshold)
        if self._write_csv:
            write_csv(self.result, self.xlsx.with_suffix(".csv"), self._csv_locale)


class _PdfCreatedHandler(FileSystemEventHandler):
    def __init__(self, folder: Path, sink: queue.Queue[Path]):
        self._folder = folder
        self._sink = sink

    def on_created(self, event: FileSystemEvent) -> None:
        self._offer(event.src_path, event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._offer(event.dest_path, event.is_directory)

    def _offer(self, raw: bytes | str, is_directory: bool) -> None:
        path = Path(os.fsdecode(raw))
        if not is_directory and path.parent == self._folder and _is_candidate(path):
            self._sink.put(path)


def _is_candidate(path: Path) -> bool:
    return path.suffix.lower() == ".pdf" and not path.name.startswith(".")


class FolderWatcher:
    """Processes PDFs dropped in `folder`.

    Processed files go to `folder/processed/`, files that could not be read as
    invoices to `folder/failed/`. A file is only picked up once its size has been
    stable for `settle_seconds`, so partially copied files are left alone.
    """

    def __init__(
        self,
        folder: Path,
        pipeline: Pipeline,
        report: RunningReport,
        *,
        settle_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.folder = folder.resolve()
        self.processed_dir = self.folder / PROCESSED_DIR
        self.failed_dir = self.folder / FAILED_DIR
        self._pipeline = pipeline
        self._report = report
        self._settle = settle_seconds
        self._clock = clock

    def handle(self, path: Path) -> Invoice | FileIssue:
        """Process one file, record it in the report and move it out of the way."""
        outcome = self._pipeline.process_pdf(path)
        self._report.add(outcome)
        destination = self.failed_dir if isinstance(outcome, FileIssue) else self.processed_dir
        moved = _move_unique(path, destination)
        log.info(
            "file filed",
            extra={"file": path.name, "to": f"{destination.name}/{moved.name}"},
        )
        return outcome

    def scan_existing(self) -> list[Path]:
        return list_pdfs(self.folder)

    def run(self, stop: threading.Event, process_existing: bool = True) -> None:
        """Block until `stop` is set, processing files as they become stable."""
        self.processed_dir.mkdir(exist_ok=True)
        self.failed_dir.mkdir(exist_ok=True)
        arrivals: queue.Queue[Path] = queue.Queue()
        observer = Observer()
        observer.schedule(_PdfCreatedHandler(self.folder, arrivals), str(self.folder))
        observer.start()
        log.info("watching folder", extra={"folder": str(self.folder)})
        pending: dict[Path, tuple[int, float]] = {}
        skipped: dict[Path, int] = {}
        for path in self.scan_existing():
            if process_existing:
                arrivals.put(path)
            else:  # some platforms replay recent events at startup, so remember what was here
                skipped[path] = path.stat().st_mtime_ns
        try:
            while not stop.is_set():
                self._drain(arrivals, pending, skipped)
                self._process_stable(pending)
                stop.wait(TICK_SECONDS)
        finally:
            observer.stop()
            observer.join()
            log.info("watcher stopped")

    def _drain(
        self,
        arrivals: queue.Queue[Path],
        pending: dict[Path, tuple[int, float]],
        skipped: dict[Path, int],
    ) -> None:
        while True:
            try:
                path = arrivals.get_nowait()
            except queue.Empty:
                return
            try:
                if skipped.get(path) == path.stat().st_mtime_ns:
                    continue
            except FileNotFoundError:
                continue
            pending.setdefault(path, (-1, self._clock()))

    def _process_stable(self, pending: dict[Path, tuple[int, float]]) -> None:
        now = self._clock()
        for path in list(pending):
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                del pending[path]
                continue
            last_size, since = pending[path]
            if size != last_size:
                pending[path] = (size, now)
            elif now - since >= self._settle:
                del pending[path]
                try:
                    self.handle(path)
                except Exception:
                    log.exception("could not process file", extra={"file": path.name})


def _move_unique(path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / path.name
    counter = 1
    while target.exists():
        target = directory / f"{path.stem}.{counter}{path.suffix}"
        counter += 1
    shutil.move(path, target)
    return target

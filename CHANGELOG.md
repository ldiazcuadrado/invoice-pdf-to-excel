# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-15

### Added

- Extraction of supplier, tax ID, invoice number, date, tax lines, IRPF and total from Spanish PDF invoices, with a rule-based extractor covering several layouts.
- Multiple VAT rates per invoice, modelled as tax lines, with recargo de equivalencia; validation per line and in total.
- OCR for image-only PDFs through pypdfium2 and Tesseract (`ocr` extra), with language detection and fallback to English.
- Optional LLM extractor using the Anthropic SDK with a forced tool call. Disabled by default and gated behind `INVOICE_LLM_ENABLED`.
- Per-field and per-invoice confidence scores; low-confidence cells highlighted in the Excel report.
- NIF, CIF and NIE check-character validation and duplicate detection.
- Excel report with invoices, tax lines, quarterly input VAT (modelo 303), annual supplier totals with the modelo 347 threshold flag, and issues; CSV export in `en` and `es` locales.
- `invoice-to-excel` CLI with `process`, `watch` and `serve` commands.
- Watch-folder mode that appends to a running report and sorts files into `processed/` and `failed/`.
- Single-page web UI built on FastAPI: drag and drop, progress, preview, Excel and CSV download. No server-side persistence.
- Configuration through `INVOICE_*` environment variables and `.env`, structured logging (text or JSON).
- Dockerfile (multi-stage, non-root, Tesseract with Spanish data), `docker-compose.yml`, GitHub Actions CI and Dependabot.
- Fictitious Spanish-format test invoices, including a scanned PDF, and a generator script.

[Unreleased]: https://github.com/ldiazcuadrado/invoice-pdf-to-excel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ldiazcuadrado/invoice-pdf-to-excel/releases/tag/v0.1.0

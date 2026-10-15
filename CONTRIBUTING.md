# Contributing

## Setup

```bash
uv sync --all-extras
uv run pre-commit install
```

OCR tests need Tesseract (`brew install tesseract tesseract-lang` or `apt install tesseract-ocr tesseract-ocr-spa`).

## Checks

All of these must pass before a change is merged; CI runs the same commands.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

## Guidelines

- Keep the module boundaries: extraction produces an `Invoice`, validation judges it, reporting presents it.
- New extraction rules need a fictitious fixture in `scripts/generate_test_data.py` and a test. Regenerate `test_data/invoices` and `reports/` when the generator changes.
- Never commit real invoices or real tax IDs. Test data must be obviously fictitious.
- Tests must not use the network. Use a fake client for anything that would call an API.
- Anything that sends data to a third party must stay opt-in and be documented under "Privacy" in the README.
- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Update `CHANGELOG.md` for user-visible changes.

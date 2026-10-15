from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from invoice_to_excel.config import Settings
from invoice_to_excel.extraction import Pipeline, build_pipeline
from invoice_to_excel.models import Invoice, Result

ROOT = Path(__file__).resolve().parents[1]
HAS_TESSERACT = shutil.which("tesseract") is not None

requires_tesseract = pytest.mark.skipif(not HAS_TESSERACT, reason="Tesseract is not installed")


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "generate_test_data", ROOT / "scripts" / "generate_test_data.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_test_data"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep developer .env files and INVOICE_* variables out of the tests."""
    import os

    for name in list(os.environ):
        if name.startswith("INVOICE_") or name == "ANTHROPIC_API_KEY":
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(scope="session")
def invoice_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The fictitious test PDFs, regenerated into a temporary folder."""
    target = tmp_path_factory.mktemp("invoices")
    load_generator().generate(target)
    return target


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def pipeline(settings: Settings) -> Pipeline:
    return build_pipeline(settings)


@pytest.fixture(scope="session")
def result(invoice_folder: Path) -> Result:
    return build_pipeline(Settings(_env_file=None)).process_folder(invoice_folder)


@pytest.fixture(scope="session")
def by_file(result: Result) -> dict[str, Invoice]:
    return {i.filename: i for i in result.invoices}

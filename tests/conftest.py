"""Shared test setup.

- Replaces the AI model with tests/fake_llm.py (no API key or quota needed).
- Gives every test its own empty storage folder.
- Uses PostgreSQL only when TEST_DATABASE_URL is set (never the real DATABASE_URL from your secrets),
  and empties its tables before each test.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ.pop("DATABASE_URL", None)

import fake_llm  # noqa: E402,F401  (patches pipeline.LLM)

import store  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL", "")


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(store, "DATA", str(data))
    monkeypatch.setattr(store, "USERS", str(data / "users.json"))
    monkeypatch.setattr(store, "FAILS", str(data / "security" / "login_failures.json"))
    monkeypatch.setattr(store, "USAGE", str(data / "security" / "usage.json"))
    monkeypatch.setattr(store, "database_url", lambda: TEST_DB)      # never read secrets.toml in tests
    if TEST_DB:
        db = store._pg()
        with db.pool.connection() as c:
            c.execute("TRUNCATE users, channels, projects, project_files, login_failures, usage, events")
    fake_llm.FakeLLM.calls.clear()
    fake_llm.FakeLLM.fail_next = 0
    yield


def make_pdf(pages=None) -> bytes:
    """A small synthetic 'paper' (one paragraph per page so each becomes its own passage)."""
    import pymupdf
    pages = pages or [
        "Automatic screening of conventional Pap smears. Cervical cancer screening relies on cytology experts "
        "who examine thousands of cells in every smear by eye.",
        "We present a screening system designed to assist cytology experts. It locates cells, classifies them "
        "and flags smears for a closer look.",
        "On a private dataset of 339 expert-annotated smears the system reached an average accuracy of 88.8% "
        "in cross-validation.",
        "The dataset is small and comes from a single clinic, so the results may not generalise to other settings.",
    ]
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 520, 400), text, fontsize=11)
    return doc.tobytes()


@pytest.fixture
def pdf_bytes():
    return make_pdf()


@pytest.fixture
def app_path():
    return str(ROOT / "app.py")

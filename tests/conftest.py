"""Offline fixtures; collection never imports product drivers or writes files."""
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Confine any per-test persistence to pytest's temporary directory."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def repo_root(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    return REPO_ROOT

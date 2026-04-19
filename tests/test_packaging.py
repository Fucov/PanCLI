from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pancli


def test_version_export() -> None:
    assert isinstance(pancli.__version__, str)
    assert pancli.__version__


def test_python_m_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pancli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "pancli" in result.stdout.lower()


def test_pyproject_has_console_script() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'pancli = "pancli.main:main"' in text

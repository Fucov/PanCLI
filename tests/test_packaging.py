from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pansh


def test_version_export() -> None:
    assert isinstance(pansh.__version__, str)
    assert pansh.__version__


def test_python_m_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pansh", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "pansh" in result.stdout.lower()


def test_pyproject_has_console_script() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'pansh = "pansh.main:main"' in text
